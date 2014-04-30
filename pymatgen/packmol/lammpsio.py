#!/usr/bin/env python

import re
import numpy as np
from pymatgen.serializers.json_coders import MSONable


def _list2float(seq):
    for x in seq:
        try:
            yield float(x)
        except ValueError:
            yield x


class LammpsLog(MSONable):
    """
    Parser for LAMMPS log file (parse function).
    Saves the output properties (log file) in the form of a dictionary (LOG) with the key being
    the LAMMPS output property (see 'thermo_style custom' command in the LAMMPS documentation).
    For example, LOG['temp'] will return the temperature data array in the log file.
    """

    def __init__(self, llog, ave):
        """
        Args:
            llog:
                Dictionary of lamps log
            ave:
                Dictionary of averages
        """

        self.llog = llog  # Dictionary LOG has all the output property data as numpy 1D arrays with the property name as the key
        self.ave = ave  # Dictionary of averages (AJ asks: What is this???)

    @classmethod
    def from_file(cls, filename):
        """
        Parses the log file. 
        """
        md = 0  # To avoid reading the minimization data steps
        header = 0
        footer_blank_line = 0
        llog = {}
        ave = {}

        with open(filename, 'r') as logfile:
            total_lines = len(logfile.readlines())
            logfile.seek(0)

            for line in logfile:

                # total steps of MD
                steps = re.search('run\s+([0-9]+)', line)
                if steps:
                    md_step = float(steps.group(1))
                    md = 1

                # save freq to log
                thermo = re.search('thermo\s+([0-9]+)', line)
                if thermo:
                    log_save_freq = float(thermo.group(1))

                # log format
                format = re.search('thermo_style.+', line)
                if format:
                    data_format = format.group().split()[2:]

                if all(isinstance(x, float) for x in
                       list(_list2float(line.split()))) and md == 1: break

                header += 1

            # TODO: review this code. AJ thinks footer_blank_line will not work??
            for line in logfile:
                if line == '\n':
                    footer_blank_line += 1

            rawdata = np.genfromtxt(fname=filename, dtype=float, skip_header=header,
                                    skip_footer=total_lines - (
                                    header + md_step / log_save_freq + 1) - footer_blank_line)

            for column, property in enumerate(data_format):
                llog[property] = rawdata[:, column]

            # calculate the average
            for key in llog.keys():
                ave[str(key)] = np.mean(llog[key])

            return LammpsLog(llog, ave)

    def list_properties(self):
        """
        print the list of properties
        """
        print log.llog.keys()

    @property
    def to_dict(self):
        return {"@module": self.__class__.__module__,
                "@class": self.__class__.__name__,
                "llog": self.llog,
                "ave": self.ave}

    @classmethod
    def from_dict(cls, d):
        return LammpsLog(d['llog'], d['ave'])


if __name__ == '__main__':
    filename = 'log.test'
    log = LammpsLog.from_file(filename)
    #print log.LOG.keys()
    #print log.llog
    log.list_properties()
    #print np.mean(log.LOG['step'])
    #print log.ave['step']
