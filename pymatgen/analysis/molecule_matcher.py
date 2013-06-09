#!/usr/bin/env python

"""
This module provides classes to perform fitting of molecule with arbitrary
atom orders.
"""

__author__ = "Xiaohui Qu"
__copyright__ = "Copyright 2011, The Materials Project"
__version__ = "1.0"
__maintainer__ = "Xiaohui Qu"
__email__ = "xhqu1981@gmail.com"
__status__ = "Experimental"
__date__ = "Jun 7, 2013"

import re
import math
import abc
import itertools

from pymatgen.serializers.json_coders import MSONable
from pymatgen.util.decorators import requires
from pymatgen.io.babelio import BabelMolAdaptor
try:
    import openbabel as ob
except ImportError:
    ob = None


class AbstractMolAtomMapper(MSONable):
    """
    Abstract molecular atom order mapping class. A mapping will be able to
    find the uniform
    atom order of two molecules that can pair the geometrically equivalent
    atoms.
    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def uniform_labels(self, mol1, mol2):
        """
        Pair the geometrically equivalent atoms of the molecules.

        Args:
            mol1:
                First molecule. OpenBabel OBMol or pymatgen Molecule object.
            mol2:
                Second molecule. OpenBabel OBMol or pymatgen Molecule object.

        Returns:
            (list1, list2) if uniform atom order is found. list1 and list2
            are for mol1 and mol2, respectively. Their length equal
            to the number of atoms. They represents the uniform atom order
            of the two molecules. The
            value of each element is the original atom index in mol1 or mol2
             of the current atom in
            uniform atom order.
            (None, None) if unform atom is not available.
        """
        pass

    @abc.abstractmethod
    def get_molecule_hash(self, mol):
        """
        Defines a hash for molecules. This allows molecules to be grouped
        efficiently for comparison.

        Args:
            mol:
                The molecule. OpenBabel OBMol or pymatgen Molecule object

        Returns:
            A hashable object. Examples can be string formulas, etc.
        """
        pass

    @classmethod
    def from_dict(cls, d):
        for trans_modules in ['molecule_matcher']:
            mod = __import__('pymatgen.analysis.' + trans_modules,
                             globals(), locals(), [d['@class']], -1)
            if hasattr(mod, d['@class']):
                trans = getattr(mod, d['@class'])
                return trans()
        raise ValueError("Invalid MolAtomMapper dict")

    @property
    def to_dict(self):
        return {"version": __version__, "@module": self.__class__.__module__,
                "@class": self.__class__.__name__}


class InchiMolAtomMapper(AbstractMolAtomMapper):
    """
    Pair atoms by inchi labels.
    """

    def _inchi_labels(self, mol):
        """
        Get the inchi canonical labels of the heavy atoms in the molecule

        Args:
            mol:
            The molecule. OpenBabel OBMol object

        Returns:
            The label mappings. List of tuple of canonical label,
            original label
            List of equivalent atoms.
        """
        obConv = ob.OBConversion()
        obConv.SetOutFormat("inchi")
        obConv.AddOption("a", ob.OBConversion.OUTOPTIONS)
        obConv.AddOption("X", ob.OBConversion.OUTOPTIONS, "DoNotAddH")
        inchi_text = obConv.WriteString(mol)
        match = re.search("InChI=(?P<inchi>.+)\nAuxInfo=.+/N:(?P<labels>[0-9,]+)/(E:(?P<eq_atoms>[0-9,\(\)]*)/)?", inchi_text)
        inchi = match.group("inchi")
        label_text = match.group("labels")
        eq_atom_text = match.group("eq_atoms")
        heavy_atom_labels = tuple([int(i) for i in label_text.split(',')])
        eq_atoms = []
        if eq_atom_text is not None:
            eq_tokens = re.findall('\(((?:[0-9]+,)+[0-9]+)\)', eq_atom_text)
            eq_atoms = tuple([tuple([int(i) for i in t.split(',')])
                              for t in eq_tokens])
        return heavy_atom_labels, eq_atoms, inchi

    def _group_centroid(self, mol, ilabels, group_atoms):
        """
        Calculate the centroids of a group atoms indexed by the labels of inchi

        Args:
            mol:
                The molecule. OpenBabel OBMol object
            ilabel:
                inchi label map

        Returns:
            Centroid. Tuple (x, y, z)
        """
        c1x, c1y, c1z = 0.0, 0.0, 0.0
        for i in group_atoms:
            orig_idx = ilabels[i-1]
            oa1 = mol.GetAtom(orig_idx)
            c1x += float(oa1.x())
            c1y += float(oa1.y())
            c1z += float(oa1.z())
        num_atoms = len(group_atoms)
        c1x /= num_atoms
        c1y /= num_atoms
        c1z /= num_atoms
        return c1x, c1y, c1z

    def _virtual_molecule(self, mol, ilabels, eq_atoms, farthest_group_idx):
        """
        Create a virtual molecule by unique atoms, the centriods of the
        equivalent
        atoms, special atoms

        Args:
            mol:
                The molecule. OpenBabel OBMol object
            ilables:
                inchi label map
            eq_atoms:
                equivalent atom labels
            farthest_group_idx:
                The equivalent atom group index in which there is the
                farthest atom
                to the centroid

        Return:
            The virtual molecule
        """
        vmol = ob.OBMol()

        non_unique_atoms = set([a for g in eq_atoms for a in g])
        all_atoms = set(range(1, len(ilabels) + 1))
        unique_atom_labels = sorted(all_atoms - non_unique_atoms)

        #try to align molecules using unique atoms
        for i in unique_atom_labels:
            orig_idx = ilabels[i-1]
            oa1 = mol.GetAtom(orig_idx)
            a1 = vmol.NewAtom()
            a1.SetAtomicNum(oa1.GetAtomicNum())
            a1.SetVector(oa1.GetVector())

        #try to align using centroids of the equivalent atoms
        if vmol.NumAtoms() < 3:
            for symm in eq_atoms:
                c1x, c1y, c1z = self._group_centroid(mol, ilabels, symm)
                a1 = vmol.NewAtom()
                a1.SetAtomicNum(9)
                a1.SetVector(c1x, c1y, c1z)

        # if no enough point, add the farthest atom to centriod of the first
        # equivalent atoms group
        if vmol.NumAtoms() < 3:
            symm = eq_atoms[farthest_group_idx-1]
            orig_farthest_idx = ilabels[symm[0]-1]
            farthest_distance = 0.0
            c1x, c1y, c1z = self._group_centroid(mol, ilabels, symm)
            for i in symm:
                orig_idx = ilabels[i-1]
                oa1 = mol.GetAtom(orig_idx)
                current_distance = math.sqrt((c1x - float(oa1.x())) ** 2 +
                                             (c1y - float(oa1.y())) ** 2 +
                                             (c1z - float(oa1.z())) ** 2)
                if current_distance > farthest_distance:
                    farthest_distance = current_distance
                    orig_farthest_idx = orig_idx
            a1 = vmol.NewAtom()
            a1.SetAtomicNum(9)
            a1.SetVector(mol.GetAtom(orig_farthest_idx).GetVector())
            orig_last_idx = orig_farthest_idx

            if vmol.NumAtoms() < 3:
                # only 1 symm group in the original molecule
                # find a nearest atom connected to the last atom
                # all other atoms are hydrogen, a hydrogen can't connect two
                # atoms so they must be bonded to each other
                if vmol.NumAtoms() != 2:
                    raise Exception("Design Error! No enough atoms")
                orig_nearest_idx = ilabels[eq_atoms[0][0]-1]
                nearest_distance = float("inf")
                for symm in eq_atoms:
                    for i in symm:
                        orig_idx = ilabels[i-1]
                        oa1 = mol.GetAtom(orig_idx)
                        if oa1.IsConnected(mol.GetAtom(orig_last_idx)) \
                                and (orig_idx != orig_last_idx):
                            current_distance = oa1.GetDistance(orig_last_idx)
                            if current_distance < nearest_distance:
                                nearest_distance = current_distance
                                orig_nearest_idx = orig_idx
                a1 = vmol.NewAtom()
                a1.SetAtomicNum(9)
                a1.SetVector(mol.GetAtom(orig_nearest_idx).GetVector())
        return vmol

    def _largest_radius_group_idx(self, mol, ilabels, eq_atoms):
        """
        Find the equivalent atom group index which the farthest atom to
        centroid is located in.

        Args:
            mol:
                The molecule. OpenBabel OBMol object
            ilabels:
                inchi lable map
            eq_atom:
                equivalent atoms

        Return:
            tuple (group index, largest distance). Group index starts from 1.
        """
        farthest_group_idx = 0
        farthest_distance = 0.0
        for current_group_idx, symm in enumerate(eq_atoms):
            c1x, c1y, c1z = self._group_centroid(mol, ilabels, symm)
            for i in symm:
                orig_idx = ilabels[i-1]
                oa1 = mol.GetAtom(orig_idx)
                current_distance = math.sqrt((c1x - float(oa1.x())) ** 2 +
                                             (c1y - float(oa1.y())) ** 2 +
                                             (c1z - float(oa1.z())) ** 2)
                if current_distance > farthest_distance:
                    farthest_distance = current_distance
                    farthest_group_idx = current_group_idx
        return farthest_group_idx + 1, farthest_distance

    def _align_heavy_atoms(self, mol1, mol2, ilabel1, ilabel2, eq_atoms):
        """
        Align the label of topologically identical atoms of second molecule
        towards first molecule

        Args:
            mol1:
                First molecule. OpenBabel OBMol object
            mol2:
                Second molecule. OpenBabel OBMol object
            ilabel1:
                inchi label map of the first molecule
            ilabel2:
                inchi label map of the second molecule
            eq_atoms:
                equivalent atom lables

        Return:
            corrected inchi labels of heavy atoms of the second molecule
        """

        farthest_group_idx, farthest_distance = \
            self._largest_radius_group_idx(mol1, ilabel1, eq_atoms)
        farthest_group_idx2, farthest_distance2 = \
            self._largest_radius_group_idx(mol2, ilabel2, eq_atoms)
        if farthest_distance2 > farthest_distance:
            farthest_distance = farthest_distance2
            farthest_group_idx = farthest_group_idx2

        vmol1 = self._virtual_molecule(mol1, ilabel1, eq_atoms,
                                       farthest_group_idx)
        vmol2 = self._virtual_molecule(mol2, ilabel2, eq_atoms,
                                       farthest_group_idx)

        nvirtual = vmol1.NumAtoms()
        nheavy = len(ilabel1)

        for i in ilabel2:  # add all heavy atoms
            a1 = vmol1.NewAtom()
            a1.SetAtomicNum(1)
            a1.SetVector(0.0, 0.0, 0.0)  # useless, just to pair with vmol2
            oa2 = mol2.GetAtom(i)
            a2 = vmol2.NewAtom()
            a2.SetAtomicNum(1)
            # align using the virtual atoms, these atoms are not
            # used to align, but match by positions
            a2.SetVector(oa2.GetVector())

        aligner = ob.OBAlign(False, False)
        aligner.SetRefMol(vmol1)
        aligner.SetTargetMol(vmol2)
        aligner.Align()
        aligner.UpdateCoords(vmol2)

        canon_mol1 = ob.OBMol()
        for i in ilabel1:
            oa1 = mol1.GetAtom(i)
            a1 = canon_mol1.NewAtom()
            a1.SetAtomicNum(oa1.GetAtomicNum())
            a1.SetVector(oa1.GetVector())

        aligned_mol2 = ob.OBMol()
        for i in range(nvirtual + 1, nvirtual + nheavy + 1):
            oa2 = vmol2.GetAtom(i)
            a2 = aligned_mol2.NewAtom()
            a2.SetAtomicNum(oa2.GetAtomicNum())
            a2.SetVector(oa2.GetVector())

        canon_label2 = range(1, nheavy+1)
        for symm in eq_atoms:
            for i in symm:
                canon_label2[i-1] = -1
        for symm in eq_atoms:
            candidates1 = list(symm)
            candidates2 = list(symm)
            for c2 in candidates2:
                distance = 99999.0
                canon_idx = candidates1[0]
                a2 = aligned_mol2.GetAtom(c2)
                for c1 in candidates1:
                    a1 = canon_mol1.GetAtom(c1)
                    d = a1.GetDistance(a2)
                    if d < distance:
                        distance = d
                        canon_idx = c1
                canon_label2[c2-1] = canon_idx
                candidates1.remove(canon_idx)

        canon_inchi_orig_map2 = [(canon, inchi, orig)
                                 for canon, inchi, orig in
                                 zip(canon_label2, range(1, nheavy + 1),
                                     ilabel2)]
        canon_inchi_orig_map2.sort(key=lambda x: x[0])
        heavy_atom_indices2 = tuple([x[2] for x in canon_inchi_orig_map2])
        return heavy_atom_indices2

    def _align_hydrogen_atoms(self, mol1, mol2, heavy_indices1,
                              heavy_indices2):
        """
        Align the label of topologically identical atoms of second molecule
        towards first molecule

        Args:
            mol1:
                First molecule. OpenBabel OBMol object
            mol2:
                Second molecule. OpenBabel OBMol object
            heavy_indices1:
                inchi label map of the first molecule
            heavy_indices2:
                label map of the second molecule

        Return:
            corrected label map of all atoms of the second molecule
        """
        num_atoms = mol2.NumAtoms()
        all_atom = set(range(1, num_atoms+1))
        hydrogen_atoms1 = all_atom - set(heavy_indices1)
        hydrogen_atoms2 = all_atom - set(heavy_indices2)
        label1 = heavy_indices1 + tuple(hydrogen_atoms1)
        label2 = heavy_indices2 + tuple(hydrogen_atoms2)

        cmol1 = ob.OBMol()
        for i in label1:
            oa1 = mol1.GetAtom(i)
            a1 = cmol1.NewAtom()
            a1.SetAtomicNum(oa1.GetAtomicNum())
            a1.SetVector(oa1.GetVector())
        cmol2 = ob.OBMol()
        for i in label2:
            oa2 = mol2.GetAtom(i)
            a2 = cmol2.NewAtom()
            a2.SetAtomicNum(oa2.GetAtomicNum())
            a2.SetVector(oa2.GetVector())

        aligner = ob.OBAlign(False, False)
        aligner.SetRefMol(cmol1)
        aligner.SetTargetMol(cmol2)
        aligner.Align()
        aligner.UpdateCoords(cmol2)

        hydrogen_label2 = []
        hydrogen_label1 = list(range(len(heavy_indices1) + 1, num_atoms + 1))
        for h2 in range(len(heavy_indices2) + 1, num_atoms + 1):
            distance = 99999.0
            idx = hydrogen_label1[0]
            a2 = cmol2.GetAtom(h2)
            for h1 in hydrogen_label1:
                a1 = cmol1.GetAtom(h1)
                d = a1.GetDistance(a2)
                if d < distance:
                    distance = d
                    idx = h1
            hydrogen_label2.append(idx)
            hydrogen_label1.remove(idx)

        hydrogen_orig_idx2 = label2[len(heavy_indices2):]
        hydrogen_canon_orig_map2 = [(canon, orig) for canon, orig
                                    in zip(hydrogen_label2,
                                           hydrogen_orig_idx2)]
        hydrogen_canon_orig_map2.sort(key=lambda x: x[0])
        hydrogen_canon_indices2 = [x[1] for x in hydrogen_canon_orig_map2]

        canon_label1 = label1
        canon_label2 = heavy_indices2 + tuple(hydrogen_canon_indices2)

        return canon_label1, canon_label2

    def _get_elements(self, mol, label):
        """
        The the elements of the atoms in the specified order

        Args:
            mol:
                The molecule. OpenBabel OBMol object.
            label:
                The atom indices. List of integers.

        Returns:
            Elements. List of integers.
        """
        elements = [int(mol.GetAtom(i).GetAtomicNum()) for i in label]
        return elements

    def uniform_labels(self, mol1, mol2):
        obmol_orig_1 = BabelMolAdaptor(mol1).openbabel_mol
        obmol_orig_2 = BabelMolAdaptor(mol2).openbabel_mol

        ilabel1, iequal_atom1, inchi1 = self._inchi_labels(obmol_orig_1)
        ilabel2, iequal_atom2, inchi2 = self._inchi_labels(obmol_orig_2)

        if inchi1 != inchi2:
            return None, None  # Topoligically different

        if iequal_atom1 != iequal_atom2:
            raise Exception("Design Error! Equavilent atoms are inconsistent")

        heavy_atom_indices2 = self._align_heavy_atoms(obmol_orig_1,
                                                      obmol_orig_2, ilabel1,
                                                      ilabel2, iequal_atom1)
        clabel1, clabel2 = self._align_hydrogen_atoms(obmol_orig_1,
                                                      obmol_orig_2, ilabel1,
                                                      heavy_atom_indices2)

        elements1 = self._get_elements(obmol_orig_1, clabel1)
        elements2 = self._get_elements(obmol_orig_2, clabel2)

        if elements1 != elements2:
            raise Exception("Design Error! Atomic elements are inconsistent")

        return clabel1, clabel2

    def get_molecule_hash(self, mol):
        """
        Return inchi as molecular hash
        """
        obmol = BabelMolAdaptor(mol).openbabel_mol
        inchi = self._inchi_labels(obmol)[2]
        return inchi


class MoleculeMatcher(MSONable):
    """
    Class to match molecules and identify whether molecules are the same.
    """

    def __init__(self, tolerance=0.01, mapper=InchiMolAtomMapper()):
        """
        Args:
            tolerance:
                RMSD difference threshold whether two molecules are different
            mapper:
                MolAtomMapper object that is able to map the atoms of two
                molecule to
                uniform order
        """
        self._tolerance = tolerance
        self._mapper = mapper

    def fit(self, mol1, mol2):
        """
        Fit two molecules.

        Args:
            mol1:
                First molecule. OpenBabel OBMol or pymatgen Molecule object
            mol2:
                Second molecule. OpenBabel OBMol or pymatgen Molecule object

        Returns:
            A boolean value indicates whether two molecules are the same.
        """
        return self.get_rmsd(mol1, mol2) < self._tolerance

    def get_rmsd(self, mol1, mol2):
        """
        Get RMSD between two molecule with arbitrary atom order.

        Returns:
            RMSD if topology of the two molecules are the same
            Infinite if  the topology is different
        """
        label1, label2 = self._mapper.uniform_labels(mol1, mol2)
        if label1 is None or label2 is None:
            return float("Inf")
        return self._calc_rms(mol1, mol2, label1, label2)

    def _calc_rms(self, mol1, mol2, clabel1, clabel2):
        """
        Calculate the RMSD.

        Args:
            mol1:
                The first molecule. OpenBabel OBMol or pymatgen Molecule object
            mol2:
                The second molecule. OpenBabel OBMol or pymatgen Molecule
                object
            clabel1:
                The atom indices that can reorder the first molecule to
                uniform atom order
            clabel1:
                The atom indices that can reorder the second molecule to
                uniform atom order

        Returns:
            The RMSD.
        """
        obmol1 = BabelMolAdaptor(mol1).openbabel_mol
        obmol2 = BabelMolAdaptor(mol2).openbabel_mol

        cmol1 = ob.OBMol()
        for i in clabel1:
            oa1 = obmol1.GetAtom(i)
            a1 = cmol1.NewAtom()
            a1.SetAtomicNum(oa1.GetAtomicNum())
            a1.SetVector(oa1.GetVector())
        cmol2 = ob.OBMol()
        for i in clabel2:
            oa2 = obmol2.GetAtom(i)
            a2 = cmol2.NewAtom()
            a2.SetAtomicNum(oa2.GetAtomicNum())
            a2.SetVector(oa2.GetVector())

        aligner = ob.OBAlign(True, False)
        aligner.SetRefMol(cmol1)
        aligner.SetTargetMol(cmol2)
        aligner.Align()
        return aligner.GetRMSD()

    def group_molecules(self, mol_list):
        """
        Group molecules by structural equality.

        Args:
            mol_list:
                List of OpenBabel OBMol or pymatgen objects

        Returns:
            A list of lists of matched molecules
            Assumption: if s1=s2 and s2=s3, then s1=s3
            This may not be true for small tolerances.
        """
        mol_hash = [(i, self._mapper.get_molecule_hash(m))
                    for i, m in enumerate(mol_list)]
        mol_hash.sort(key=lambda x: x[1])

        #Use molecular hash to pre-group molecules.
        raw_groups = tuple([tuple([m[0] for m in g]) for k, g
                            in itertools.groupby(mol_hash,
                                                 key=lambda x: x[1])])

        group_indices = []
        for rg in raw_groups:
            mol_eq_test = [(p[0], p[1], self.fit(mol_list[p[0]],
                                                 mol_list[p[1]]))
                           for p in itertools.combinations(sorted(rg), 2)]
            mol_eq = set([(p[0], p[1]) for p in mol_eq_test if p[2]])
            not_alone_mols = set(itertools.chain.from_iterable(mol_eq))
            alone_mols = set(rg) - not_alone_mols
            group_indices.extend([[m] for m in alone_mols])
            while len(not_alone_mols) > 0:
                current_group = set([not_alone_mols.pop()])
                while len(not_alone_mols) > 0:
                    candidate_pairs = set(
                        [tuple(sorted(p)) for p
                         in itertools.product(current_group, not_alone_mols)])
                    mutual_pairs = candidate_pairs & mol_eq
                    if len(mutual_pairs) == 0:
                        break
                    mutual_mols = set(itertools.chain.from_iterable(mutual_pairs))
                    current_group |= mutual_mols
                    not_alone_mols -= mutual_mols
                group_indices.append(sorted(current_group))

        group_indices.sort(key=lambda x: (len(x), -x[0]), reverse=True)
        all_groups = [[mol_list[i] for i in g] for g in group_indices]
        return all_groups

    @property
    def to_dict(self):
        return {"version": __version__, "@module": self.__class__.__module__,
                "@class": self.__class__.__name__,
                "tolerance": self._tolerance, "mapper": self._mapper}

    @classmethod
    def from_dict(cls, d):
        return MoleculeMatcher(
            tolerance=d["tolerance"],
            mapper=AbstractMolAtomMapper.from_dict(d["mapper"]))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Check whether two molecules have the same structure and"
                    " geometry or group molecule for more than 3 molecules")
    parser.add_argument("molecules", type=argparse.FileType(mode='r'),
                        nargs='+', metavar="FILENAME",
                        help="The filename of the two molecules to be compared")
    parser.add_argument("-f", "--format", dest="format", default="xyz",
                        help="format of the molecule files OpenBabel abbrev.")
    parser.add_argument("-t", "--tolerance", dest="tolerance", default="0.01",
                        type=float,
                        help="tolerance of RMDS comparison, in Angstrom")
    parser.add_argument("-g", "--group_prefix", dest="group_prefix",
                        default="mol_",
                        help="prefix of file names for the output of molecular groups")
    args = parser.parse_args()

    matcher = MoleculeMatcher(args.tolerance)

    if len(args.molecules) < 2:
        parser.print_help()
        exit(0)

    if len(args.molecules) == 2:
        mol1 = BabelMolAdaptor.from_string(args.molecules[0].read(), args.format).pymatgen_mol
        mol2 = BabelMolAdaptor.from_string(args.molecules[1].read(), args.format).pymatgen_mol

        if matcher.fit(mol1, mol2):
            print "The two molecules are equal"
        else:
            print "The two molecules are different"
    else:
        mol_list = [BabelMolAdaptor.from_string(f.read(), args.format).pymatgen_mol \
                    for f in args.molecules]
        all_groups = matcher.group_molecules(mol_list)
        for i, g in enumerate(all_groups):
            for j, m in enumerate(g):
                filename = args.group_prefix + str(i) + '_' + str(j) + '.' + args.format
                BabelMolAdaptor(m).write_file(filename, args.format)