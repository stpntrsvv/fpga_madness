#!/usr/bin/env python3
"""Проверки размещения, загружаемого образа, потока и общей отмены эпохи."""
from dataclasses import replace
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'scripts'))
from field_program import (Graph, Image, LOCAL_INPUTS, Node, budget, compile_image, flux_graph,
                           heat_graph, profiles, reaction_graph, reference_graph, reference_reaction, simulate)
from grid_model import Grid, example, reference
from dpu import ONE


class ProgramTest(unittest.TestCase):
    def compare(self, grid, edge, local, profile, constants=(0,)*8, steps=1):
        image, _ = compile_image(edge, local, profile, constants)
        loaded = Image.from_bytes(image.to_bytes(), profile)
        self.assertEqual(image, loaded)
        result = simulate(grid, loaded, profile, steps)
        state, fault, completed = grid.state, 0, 0
        for _ in range(steps):
            state, fault = reference_graph(replace(grid, state=state), edge, local, constants)
            if fault:
                break
            completed += 1
        self.assertEqual((result['state'], result['completed'], result['fault']), (state, completed, fault))
        for frame in result['frames']:
            self.assertEqual(frame['stats']['trial_writes'], len(grid.state))
            self.assertEqual(frame['stats']['map_reads'], 4*len(grid.state))
            self.assertEqual(frame['stats']['state_reads_a'], len(grid.state))
            self.assertEqual(frame['stats']['state_reads_b'], len(grid.state))
        return result

    def test_two_laws_one_profile(self):
        p = profiles()[1]
        for w, h in ((4,1), (4,2), (8,5), (16,8)):
            for seed in range(4):
                g = example(w,h,seed)
                heat = self.compare(g, flux_graph(), heat_graph(), p, steps=3)
                state = g.state
                for _ in range(3):
                    state, fault = reference(replace(g,state=state))
                    self.assertEqual(fault,0)
                self.assertEqual(heat['state'],state)
                constants=(ONE//8,-ONE//16)+(0,)*6
                reaction = self.compare(g,flux_graph(),reaction_graph(),p,constants,steps=3)
                state = g.state
                for _ in range(3):
                    state, fault = reference_reaction(replace(g,state=state),*constants[:2])
                    self.assertEqual(fault,0)
                self.assertEqual(reaction['state'],state)

    def test_fixed_rtl_cycle_control(self):
        for size, expected in ((64,1047),(128,4119)):
            g = example(size,size)
            r = self.compare(g,flux_graph(),heat_graph(),profiles()[0])
            self.assertEqual(r['cycles'],expected)
        for size, expected in ((64,1072),(128,4144)):
            r = self.compare(example(size,size),flux_graph(),reaction_graph(),profiles()[1])
            self.assertEqual(r['cycles'],expected)

    def test_backpressure_and_resource_width(self):
        p = profiles()[1]
        g = example(8,5)
        fast = self.compare(g,flux_graph(),reaction_graph(),p)
        paused = self.compare(g,flux_graph(),reaction_graph(),replace(p,write_pause_period=7,write_pause_length=3))
        self.assertEqual(paused['state'],fast['state'])
        self.assertEqual(paused['cycles']-fast['cycles'],paused['frames'][0]['stats']['global_stalls'])
        slow = self.compare(g,flux_graph(),reaction_graph(),replace(p,lanes=2,banks=2))
        self.assertGreater(slow['cycles'],fast['cycles'])
        image,_=compile_image(flux_graph(),heat_graph(),p)
        with self.assertRaises(ValueError):
            simulate(g,image,replace(p,banks=1))

    def test_arbitrary_graph_and_boundary_fault_mask(self):
        p = profiles()[1]
        edge = Graph(flux_graph().inputs,(Node('sum','add','center','neighbor'),
                                        Node('scaled','mul','sum','c0')), 'scaled')
        local = Graph(LOCAL_INPUTS,(Node('diff','sub','east','north'),Node('plus','add','diff','q')), 'plus')
        self.compare(example(8,5),edge,local,p,(ONE//2,)+(0,)*7,steps=2)
        # Переполнение существует только у отсутствующих связей и должно игнорироваться.
        p=replace(p,lanes=1,banks=1)
        g=Grid(1,1,((1<<31)-1,),(0,),(0,),(ONE,),(0,),1)
        edge=Graph(flux_graph().inputs,(Node('bad','add','center','center'),),'bad')
        local=Graph(LOCAL_INPUTS,(),'zero')
        r=self.compare(g,edge,local,p)
        self.assertEqual(r['fault'],0)

    def test_atomic_faults_and_wide_increment(self):
        p=profiles()[1]
        n=16
        maximum=(1<<31)-1
        grid=Grid(4,4,(0,)*(n-1)+(ONE,),(0,)*n,(0,)*n,(ONE,)*n,(0,)*(n-1)+(maximum,),ONE)
        result=self.compare(grid,flux_graph(),heat_graph(),p)
        self.assertEqual(result['fault'],3)
        self.assertEqual(result['cycles'],n//4+48)
        grid=replace(grid,state=(maximum-2*ONE,)*n,source=(ONE,)*n)
        result=self.compare(grid,flux_graph(),heat_graph(),p,steps=4)
        self.assertEqual((result['completed'],result['fault']),(2,3))
        grid=replace(grid,state=(-(1<<31),)*n,source=(maximum,)*n,dt=2*ONE)
        result=self.compare(grid,flux_graph(),heat_graph(),p)
        self.assertEqual(result['state'],(maximum-1,)*n)
        self.assertEqual(result['fault'],0)

    def test_random_signed_and_dt(self):
        rng=random.Random(20261019)
        p=profiles()[1]
        for dt in (1,513,4096,8191,ONE,1<<30):
            n=32
            arrays=[tuple(rng.randrange(-ONE,ONE) for _ in range(n)) for _ in range(5)]
            grid=Grid(8,4,*arrays,dt)
            self.compare(grid,flux_graph(),heat_graph(),p,steps=2)
            self.compare(grid,flux_graph(),reaction_graph(),p,(ONE//8,ONE//16)+(0,)*6,steps=2)

    def test_reject_graph_and_corrupted_image(self):
        p=profiles()[1]
        for graph in (Graph(LOCAL_INPUTS,(Node('x','add','x','center'),),'x'),
                      Graph(LOCAL_INPUTS,(Node('x','sqrt','center'),),'x'),
                      Graph(LOCAL_INPUTS,(Node('center','pass','center'),),'center')):
            with self.assertRaises(ValueError):
                compile_image(flux_graph(),graph,p)
        with self.assertRaises(ValueError):
            compile_image(flux_graph(),reaction_graph(),profiles()[0])
        image,_=compile_image(flux_graph(),heat_graph(),p)
        for word in ((1<<31), 5, 1 | (7<<11), 4):
            with self.assertRaises(ValueError):
                replace(image,local=(word,)+image.local[1:]).validate(p)
        d=json.loads(image.to_bytes());d['version']=999
        with self.assertRaises(ValueError):
            Image.from_bytes(json.dumps(d).encode(),p)
        self.assertEqual(budget(profiles()[1])['dsp48'],128)
        self.assertEqual(budget(profiles()[2])['dsp48'],224)

    def test_narrow_tail_and_original_center(self):
        p=profiles()[1]
        image,_=compile_image(flux_graph(),reaction_graph(),p)
        for word in ((4|(4<<3)|(1<<11)),(4|(4<<11)),4):
            with self.assertRaises(ValueError):
                replace(image,local=image.local[:4]+(word,)+image.local[5:]).validate(p)
        with self.assertRaises(ValueError):replace(image,local_output=4).validate(p)
        late=Graph(LOCAL_INPUTS,(Node('done','mul','north','center'),),'done')
        with self.assertRaises(ValueError):compile_image(flux_graph(),late,p)
        copied=Graph(LOCAL_INPUTS,(Node('copy','pass','north'),Node('done','mul','copy','center')),'done')
        self.compare(example(8,5),flux_graph(),copied,p,steps=2)


if __name__=='__main__':
    unittest.main(verbosity=2)
