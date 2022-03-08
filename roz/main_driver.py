# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 22-Oct-2021
#
#  @author: tbowers

"""The imaginatively named main driver module

This module is part of the Roz package, written at Lowell Observatory.

This module sits in the driver's seat... literally.  It will be here that the
manual and automatic interfaces to the Roz code attach.

Furthermore, as the main driver module, it calls all of the various other
modules, as needed.  There should, therefore, be little need for cross-calling
between the other non-utility modules in this package.

This module primarily trades in... driving?
"""

# Built-In Libraries

# 3rd Party Libraries

# Internal Imports
from roz import confluence_updater as cu
from roz import database_manager as dm
from roz import gather_frames as gf
from roz import process_calibrations as pc
from roz import utils


def main(directories=None, do_science=False, skip_cals=False, mem_limit=8.192e9):
    """main This is the main function.

    This function takes the directory input, determines the instrument
    is
    in questions, and calls the appropriate run_*_cals() function.

    In the future, if Roz is employed to analyze more than calibration frames,
    other argmuments to this function will be needed, and other driving
    routines will need to be added above.

    Parameters
    ----------
    directory : `str` or `pathlib.Path` or `list` of either, optional
        The directory or directories upon which to operate [Default: None]
    do_science : `bool`, optional
        Also do QA on science frames?  [Default: False]
    skip_cals : `bool`, optional
        Do not process the calibration frames.  [Default: False]
    mem_limit : `float`, optional
        Memory limit for the image combination routine [Default: 8.192e9 bytes]
    """
    # Check if the input `directories` is just a string; --> list
    if isinstance(directories, str):
        directories = [directories]

    # Loop through the directories prvided
    for directory in directories:

        # If this directory is not extant and full of FITS files, move along
        if not gf.check_directory_okay(directory, "main()"):
            continue

        # Call the appropriate Dumbwaiter(s) to sort files and copy them for processing
        dumbwaiters = []
        if not skip_cals:
            dumbwaiters.append(gf.Dumbwaiter(directory, frameclass="calibration"))
        if do_science:
            dumbwaiters.append(gf.Dumbwaiter(directory, frameclass="science"))

        # Loop over appropriate Dumbwaiter(s):
        for dumbwaiter in dumbwaiters:

            # If empty, move along
            if dumbwaiter.empty:
                continue

            # Copy over the sorted frames to processing, and package for cold storage
            dumbwaiter.copy_frames_to_processing()
            dumbwaiter.cold_storage(testing=False)

            # Giddy up!
            run = Run(dumbwaiter, mem_limit=mem_limit)
            run.proc()


# Run Functions ==============================================================#
class Run:
    """Run Class for Running the Processing

    _extended_summary_

    Parameters
    ----------
    waiter : `roz.gather_frames.Dumbwaiter`
        The dumbwaiter holding the incoming files for processing
    mem_limit : `float`, optional
        Memory limit for the image combination routine.  [Default: None]
    """

    def __init__(self, waiter, mem_limit=None):
        # Set instance attributes
        self.waiter = waiter
        self.mem_limit = mem_limit
        self.dir = self.waiter.proc_dir
        self.flags = self.waiter.inst_flags

    def proc(self):
        """proc Process the files specified in the Dumbwaiter

        Chooses which run_* method to call based on the `frameclass`
        """
        do = f"run_{self.waiter.frameclass[:3]}"
        if hasattr(self, do) and callable(func := getattr(self, do)):
            func()

    def run_cal(self, bin_list="1x1"):
        """run_cal Run Roz on the Instrument Calibration frames

        Collect the calibration frames for the instrument represented by this
        Dumbwaiter, process them, and collect statistics into a
        `CalibrationDatabase` object.  Upload the data to an InfluxDB database,
        analyze the frames for irregularities compared to historical data, and
        send alerts, if necessary.  If desired, also update the appropriate
        Confluence page(s) for user support.

        Keyword argument `bin_list` is a default in case `check_binning` is not
        specified in the instrument flags.
        """
        # Collect the calibration frames for the processing directory
        outputs = gf.gather_cal_frames(self.dir, self.flags)

        # Parse outputs:
        if all([self.flags[key] for key in ["get_bias", "get_flats", "check_binning"]]):
            bias_cl, flat_cl, bin_list = outputs
        elif all([self.flags[key] for key in ["get_bias", "check_binning"]]):
            bias_cl, bin_list = outputs
        elif all([self.flags[key] for key in ["get_bias", "get_flats"]]):
            bias_cl, flat_cl = outputs
        elif all([self.flags[key] for key in ["get_flats", "check_binning"]]):
            flat_cl, bin_list = outputs
        elif self.flags["get_bias"]:
            bias_cl = outputs
        elif self.flags["get_flats"]:
            flat_cl = outputs
        elif self.flags["check_binning"]:
            bin_list = outputs
        else:
            raise ValueError(
                f"Somthing is very wrong with instrument flags:\n{self.flags}"
            )

        # Loop through the binning schemes used
        for binning in bin_list:

            # Print out a nice status message for those interested
            human_bin = binning.replace(" ", "x")
            print(f"Processing the database for {human_bin} binning...")

            # Process the BIAS frames to produce a reduced frame and statistics
            if self.flags["get_bias"]:
                bias_meta, bias_frame = pc.process_bias(
                    bias_cl, binning=binning, mem_limit=self.mem_limit
                )

            # Process the FLAT frames to produce statistics
            if self.flags["get_flat"]:
                flat_meta = pc.process_flats(
                    flat_cl,
                    bias_frame,
                    binning=binning,
                    instrument=self.flags["instrument"],
                )

            # Take the metadata from the calibration frames and produce DATABASE
            database = dm.build_calibration_database(
                bias_meta, flat_meta, self.flags, self.dir
            )

            # Write the contents of the database to InfluxDB
            database.write_to_influxdb()

            if self.flags["instrument"] == "lmi":
                # Update the LMI Filter Information page on Confluence
                #  Images for all binnings, values only for 2x2 binning
                cu.update_filter_characterization(
                    database, png_only=(human_bin != "2x2")
                )

    def run_sci(self):
        """run_cal Run Roz on the Instrument Science frames

        _extended_summary_
        """

###################
# TODO: Clean out these two functions and make sure they are both properly
#       represented in the above run_cal() method

def run_lmi_cals(directory, mem_limit=None):
    """run_lmi_cals Run Roz on the LMI Calibration frames

    Collect the LMI calibration frames, produce statistics, and return a list
    of `CalibrationDatabase` objects for each binning scheme in this one
    directory.

    This is the main driver routine for LMI frames, and will call all of the
    various other modules, as needed.  As such, there should be little need
    for cross-calling between the other non-utility modules in this package.

    Parameters
    ----------
    directory : `str` or `pathlib.Path`
        The directory containing LMI frames to analyze.
    mem_limit : `float`, optional
        Memory limit for the image combination routine.  [Default: None]

    Returns
    -------
    `list` of `roz.database_manager.CalibrationDatabase`
        A list of the Calibration Database objects for each binning scheme
    """
    inst_flags = utils.set_instrument_flags("lmi")

    # Collect the BIAS & FLAT frames for this directory
    bias_cl, flat_cl, bin_list = gf.gather_cal_frames(directory, inst_flags)

    db_list = {}
    # Loop through the binning schemes used
    for binning in bin_list:

        # Print out a nice status message for those interested
        human_bin = binning.replace(" ", "x")
        print(f"Processing the database for {human_bin} LMI binning...")

        # Process the BIAS frames to produce a reduced frame and statistics
        bias_meta, bias_frame = pc.process_bias(
            bias_cl, binning=binning, mem_limit=mem_limit
        )

        # Process the FLAT frames to produce statistics
        flat_meta = pc.process_flats(
            flat_cl, bias_frame, binning=binning, instrument=inst_flags["instrument"]
        )

        # Take the metadata from the BAIS and FLAT frames and produce DATABASE
        database = pc.produce_database_object(bias_meta, flat_meta, inst_flags)
        # TODO: Find a better way to do this
        database.proc_dir = directory

        # Write the contents of the database to InfluxDB
        database.write_to_influxdb()

        # Update the LMI Filter Information page on Confluence
        #  Images for all binnings, values only for 2x2 binning
        cu.update_filter_characterization(database, png_only=(human_bin != "2x2"))

        # Add the database to a dictionary containing the different binnings
        db_list[human_bin] = database

    # Return the list of database objects to the calling function
    return db_list


def run_deveny_cals(directory, mem_limit=None):
    """run_lmi_cals Run Roz on the DeVeny Calibration frames

    Collect the DeVeny calibration frames, produce statistics, and return a
    list of `CalibrationDatabase` objects for each binning scheme in this one
    directory.

    This is the main driver routine for DeVeny frames, and will call all of
    the various other modules, as needed.  As such, there should be little
    need for cross-calling between the other modules in this package.

    Parameters
    ----------
    directory : `str` or `pathlib.Path`
        The directory containing DeVeny frames to analyze.
    mem_limit : `float`, optional
        Memory limit for the image combination routine.  [Default: None]

    Returns
    -------
    `list` of `roz.database_manager.CalibrationDatabase`
        A list of the Calibration Database objects for each binning scheme
    """
    inst_flags = utils.set_instrument_flags("deveny")

    # Collect the BIAS frames for this directory
    bias_cl, bin_list = gf.gather_cal_frames(directory, inst_flags)

    db_list = {}
    # Loop through the binning schemes used
    for binning in bin_list:

        # Print out a nice status message for those interested
        human_bin = binning.replace(" ", "x")
        print(f"Processing the database for {human_bin} DeVeny binning...")

        # Process the BIAS frames to produce a reduced frame and statistics
        bias_meta = pc.process_bias(
            bias_cl, binning=bin_list[0], mem_limit=mem_limit, produce_combined=False
        )

        # Take the metadata from the BAIS frames and produce DATABASE
        database = pc.produce_database_object(bias_meta, bias_meta, inst_flags)
        # TODO: Find a better way to do this
        database.proc_dir = directory

        # Add the database to a dictionary containing the different binnings
        db_list[human_bin] = database

    # Return the list of database objects to the calling function
    return db_list


# ============================================================================#
if __name__ == "__main__":
    # Set up the environment to import the program
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(prog="main_driver", description="Roz main driver")
    parser.add_argument(
        "directory",
        metavar="dir",
        type=str,
        nargs="+",
        help="The directory or directories on which to run Roz",
    )
    parser.add_argument(
        "--science",
        action="store_true",
        help="Process the science frames, too?",
    )
    parser.add_argument(
        "--nocal",
        action="store_true",
        help="Do not process the calibration frames",
    )
    args = parser.parse_args()

    # Giddy Up!
    main(args.directory, do_science=args.science, skip_cals=args.nocal)
