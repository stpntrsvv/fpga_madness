"""Одна программа управления для RTL и платы GRD2, со сменой графов без пересборки."""
from dataclasses import replace
from pathlib import Path
import sys

from dpu import ONE
from field_program import (Graph, Image, LOCAL_INPUTS, Node, compile_image, flux_graph, heat_graph,
                           profiles, reaction_graph, reference_graph, reference_reaction)
from grid_model import example
from grid_vectors import checks as heat_checks, packet

PROFILE=profiles()[1]


def configuration(image):
    image.validate(PROFILE)
    return [(0x20,image.edge_output),(0x21,image.local_output),
            *[(0x30+i,v) for i,v in enumerate(image.constants)],
            *[(0x80+i,v) for i,v in enumerate((*image.edge,*image.local))]]


def checks():
    heat,_=compile_image(flux_graph(),heat_graph(),PROFILE)
    rows=[]
    original=heat_checks()
    for index,(request,reply,data,compare,wait) in enumerate(original):
        action=(request>>1072)&255
        address=(request>>1056)&65535
        if action==3 and address==0 and reply==0x47524431:
            reply=0x47524432
        if action==3 and address in (3,4) and reply:
            assert reply%1047==0
            reply=reply//1047*1072
        rows.append((request,reply,data,compare,wait))
        if action==1 and index<len(original)-1:
            rows.extend((packet(2,a,v),0,0,0,0) for a,v in configuration(heat))

    def add(action,address=0,value=0,words=(),reply=0,output=None,wait=0):
        data=sum((v&0xffffffff)<<(32*i) for i,v in enumerate(output or ()))
        rows.append((packet(action,address,value,words),reply,data,int(output is not None),wait))

    def program(edge,local,constants=(0,)*8):
        image,_=compile_image(edge,local,PROFILE,constants)
        # Моделируем загрузку именно сериализованного образа.
        image=Image.from_bytes(image.to_bytes(),PROFILE)
        for a,v in configuration(image):
            add(2,a,v)
            add(3,a,reply=v&0xffffffff)

    def load(grid):
        add(1)
        add(2,16,grid.dt)
        for plane,values in enumerate((grid.state,grid.east,grid.south,grid.capacity_inverse,grid.source)):
            for start in range(0,4096,32):
                add(5,plane*4096+start,32,values[start:start+32])
        add(4,value=0,reply=0xbad00003)  # Поля загружены, но граф ещё нет.

    def run(grid,edge,local,constants,steps):
        state,completed,fault=grid.state,0,0
        for _ in range(steps):
            state,fault=reference_graph(replace(grid,state=state),edge,local,constants)
            if fault:break
            completed+=1
        add(4,value=steps,wait=1200*(completed+bool(fault)))
        add(3,1,reply=(2 if completed==steps else 0)|(4 if fault else 0)|(fault<<8))
        add(3,2,reply=completed)
        add(3,3,reply=1072*(completed+bool(fault)))
        add(3,4,reply=1072 if completed or fault else 0)
        for start in range(0,4096,32):add(6,start,32,output=state[start:start+32])
        return replace(grid,state=state)

    grid=example(64,64)
    load(grid)
    edge,local,constants=flux_graph(),reaction_graph(),(ONE//8,ONE//16)+(0,)*6
    program(edge,local,constants)
    grid=run(grid,edge,local,constants,0)
    # Тот же исходный образ, что и будущий обычный C для реакции–диффузии.
    grid=run(grid,edge,local,constants,64)
    # Другой граф, те же загруженные карты и продолжение того же состояния.
    program(edge,heat_graph())
    grid=run(grid,edge,heat_graph(),(0,)*8,3)
    # Меняются также операции графа связи: теплового выбора по имени в RTL нет.
    edge=Graph(edge.inputs,(Node('sample','pass','neighbor'),),'sample')
    local=Graph(LOCAL_INPUTS,(Node('delta','sub','east','west'),Node('plus','add','delta','q')),'plus')
    program(edge,local)
    grid=run(grid,edge,local,(0,)*8,2)

    # Любая запись графа во время busy запрещена.
    program(flux_graph(),heat_graph())
    add(4,value=100000)
    add(2,0x80,0,reply=0xbad00001)
    add(2,0x30,0,reply=0xbad00001)
    add(2,0x20,15,reply=0xbad00001)
    add(1)
    add(3,1)

    grid=example(64,64)
    load(grid)
    program(flux_graph(),heat_graph())
    for a,v in ((0x80,0x80000000),(0x80,4),(0x20,4),(0x82,1|(7<<11)),
                (0x80,1),(0x86,4|(4<<3)|(1<<11)),(0x86,4|(4<<11)),(0x21,4)):
        add(2,a,v)
        add(4,value=0,reply=0xbad00003)
        program(flux_graph(),heat_graph())
    # Общая проверка ошибки активного узла, даже если выход графа равен константе.
    grid=replace(grid,state=((1<<31)-1,)*4096,east=(0,)*4096,south=(0,)*4096)
    load(grid)
    local=Graph(LOCAL_INPUTS,(Node('overflow','add','center','center'),),'zero')
    program(flux_graph(),local)
    run(grid,flux_graph(),local,(0,)*8,1)
    add(1)
    return rows


if __name__=='__main__':
    rows=checks()
    Path(sys.argv[1]).write_text(''.join(f'{p:0272x} {r:08x} {v:0256x} {c} {w}\n' for p,r,v,c,w in rows))
    print(f'Подготовлено {len(rows)} проверок программируемого поля GRD2')
