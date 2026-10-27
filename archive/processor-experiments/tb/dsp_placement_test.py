"""Границы допуска обхода размещения: каскадные DSP менять нельзя."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from fix_dsp_placement import corrected


def fixture():
    cell=dict(type='DSP48E1_DSP48E1',
        attributes={'X_ORIG_TYPE':'DSP48E1','CONSTR_ABS_Z':f'{1:032b}','CONSTR_Z':f'{6:032b}','src':'пример'},
        parameters={**{k:f'{v:032b}' for k,v in dict(AREG=1,ACASCREG=1,BREG=1,BCASCREG=1,MREG=1,PREG=0).items()},
                    'A_INPUT':'DIRECT','B_INPUT':'DIRECT'},
        connections={'A0':[1],'B0':[2],'P0':[3],'PCIN0':[],'PCOUT0':[]})
    return {'modules':{'top':{'cells':{'first':copy.deepcopy(cell),'last':copy.deepcopy(cell)}}}}


class Placement(unittest.TestCase):
    def test_only_constraints(self):
        original=fixture();design=copy.deepcopy(original)
        self.assertEqual(corrected(design,2),2)
        for name,cell in design['modules']['top']['cells'].items():
            old=original['modules']['top']['cells'][name]
            self.assertEqual(cell['parameters'],old['parameters'])
            self.assertEqual(cell['connections'],old['connections'])
            self.assertEqual(cell['type'],old['type'])
            self.assertEqual(cell['attributes'],{'X_ORIG_TYPE':'DSP48E1','src':'пример'})

    def test_cascade_rejected_before_mutation(self):
        design=fixture();design['modules']['top']['cells']['last']['connections']['PCIN0']=[7]
        old=copy.deepcopy(design)
        with self.assertRaises(ValueError):corrected(design,2)
        self.assertEqual(design,old)

    def test_unknown_constraints_and_modes(self):
        for bank,key,value in (('attributes','CONSTR_Z',f'{25:032b}'),
                               ('attributes','CONSTR_PARENT','first'),
                               ('parameters','AREG',f'{0:032b}'),
                               ('parameters','A_INPUT','CASCADE')):
            with self.subTest(key=key):
                design=fixture();design['modules']['top']['cells']['last'][bank][key]=value
                old=copy.deepcopy(design)
                with self.assertRaises(ValueError):corrected(design,2)
                self.assertEqual(design,old)

    def test_count(self):
        with self.assertRaises(ValueError):corrected(fixture(),128)


if __name__=='__main__':unittest.main()
