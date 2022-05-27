# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 23-Sep-2021
#
#  @author: tbowers

"""Process the Calibration Frames for 1 Night for specified instrument

This module is part of the Roz package, written at Lowell Observatory.

This module takes the gathered calibration frames from a night (as collected by
roz.gather_frames) and performs basic data processing (bias & overscan
subtraction) before gathering statistics.  The statistics are then stuffed into
a database object (from roz.database_manager) for later use.

This module primarily trades in AstroPy Table objects (`astropy.table.Table`)
and CCDPROC Image File Collections (`ccdproc.ImageFileCollection`), along with
the odd AstroPy CCDData object (`astropy.nddata.CCDData`) and basic python
dictionaries (`dict`).
"""

# Built-In Libraries
import os
import warnings

# 3rd Party Libraries
from astropy.stats import mad_std
from astropy.table import Table
from astropy.wcs import FITSFixedWarning
import ccdproc as ccdp
from ccdproc.utils.slices import slice_from_string as get_slice
import numpy as np
from tqdm import tqdm

# Internal Imports
from roz import gather_frames
from roz import msgs
from roz import utils

# Silence Superflous AstroPy FITS Header Warnings
warnings.simplefilter("ignore", FITSFixedWarning)


class CalibContainer:
    """Class for containing and processing calubration frames

    This container holds the gathered calibration frames in the processing
    directory, as well as the processing routines needed for the various types
    of frames.  The class holds the general information needed by all
    processing methods.

    Parameters
    ----------
    directory : `pathlib.Path`
        Processing directory
    inst_flags : `dict`
        Dictionary of instrument flags from utils.set_instrument_flags()
    debug : `bool`, optional
        Print debugging statements? [Default: True]
    mem_limit : `float`, optional
        Memory limit for the image combination routine [Default: 8.192e9 bytes]
    """

    def __init__(
        self,
        directory,
        inst_flag,
        debug=True,
        mem_limit=8.192e9,
    ):
        # Parse in arguments
        self.directory = directory
        self.flags = inst_flag
        self.debug = debug
        self.mem_limit = mem_limit

        # Get the frame dictionary to be used
        self.frame_dict = gather_frames.gather_cal_frames(self.directory, self.flags)

    def process_bias(self, ccd_bin, combine_method="average"):
        """Process and combine available bias frames

        [extended_summary]

        Parameters
        ----------
        ccd_bin : `str`, optional
            Binning of the CCD -- must be specified by the caller [Default: None]
        combine_method : `str`, optional
            Combination method to pass to `ccdp.combine()`  [Default: average]

        Returns
        -------
        `astropy.table.Table`
            A table containing information about the bias frames for analysis
        `astropy.nddata.CCDData` or `NoneType`
            The combined, overscan-subtracted bias frame (if
            `produce_combined == True` else None)
        """
        # Parse instance attributes into expected variables
        bias_cl = self._check_ifc("bias_cl", ccd_bin)
        produce_combined = self.flags["get_flat"]

        if not bias_cl.files:
            return (Table(), None) if produce_combined else Table()
        if self.debug:
            msgs.info("Processing bias frames...")

        # Show progress bar for processing bias frames
        progress_bar = tqdm(
            total=len(bias_cl.files), unit="frame", unit_scale=False, colour="#bbc4cc"
        )

        # Loop through files
        bias_ccds, metadata, coord_arrays = [], [], None
        for ccd, fname in bias_cl.ccds(bitpix=16, return_fname=True):

            hdr = ccd.header
            # For BIAS set header FILTERS keyword to "DARK"
            hdr["FILTERS"] = "DARK"
            hdr["SHORT_FN"] = fname.split(os.sep)[-1]
            data = ccd.data[get_slice(hdr["TRIMSEC"], fits_convention=True)]

            # Statistics, statistics, statistics!!!!
            quadsurf, coord_arrays = utils.fit_quadric_surface(data, coord_arrays)
            metadata.append(base_metadata_dict(hdr, data, quadsurf))

            # Fit the overscan section, subtract it, then trim the image
            #  Append this to a list, update the progress bar and repeat!
            bias_ccds.append(utils.trim_oscan(ccd, hdr["BIASSEC"], hdr["TRIMSEC"]))
            progress_bar.update(1)

        progress_bar.close()

        # Convert the list of dicts into a Table and return, plus combined bias
        combined = None
        if produce_combined:
            if self.debug:
                msgs.info(f"Doing {combine_method} combine of biases now...")
            # Silence RuntimeWarning issued related to means of empty slices
            warnings.simplefilter("ignore", RuntimeWarning)
            combined = ccdp.combine(
                bias_ccds,
                method=combine_method,
                sigma_clip=True,
                mem_limit=self.mem_limit,
                sigma_clip_dev_func=mad_std,
            )
            # Reinstate RuntimeWarning
            warnings.simplefilter("default", RuntimeWarning)
        return Table(metadata), combined

    def process_dark(self, ccd_bin, combine_method="average"):
        """Process and combine available dark frames

        NOTE: Not yet implemented -- Boilerplate below is from process_bias
            tqdm color should be "#00008b"
        """
        # Parse instance attributes into expected variables
        dark_cl = self._check_ifc("dark_cl", ccd_bin)
        produce_combined = self.flags["get_flat"]

        if not dark_cl.files:
            return (Table(), None) if produce_combined else Table()
        if self.debug:
            msgs.info("Processing dark frames...")

        dark_ccds, metadata, _ = [], [], None
        # Convert the list of dicts into a Table and return, plus combined bias
        combined = None
        if produce_combined:
            if self.debug:
                msgs.info(f"Doing {combine_method} combine of darks now...")
            # Silence RuntimeWarning issued related to means of empty slices
            warnings.simplefilter("ignore", RuntimeWarning)
            combined = ccdp.combine(
                dark_ccds,
                method=combine_method,
                sigma_clip=True,
                mem_limit=self.mem_limit,
                sigma_clip_dev_func=mad_std,
            )
            # Reinstate RuntimeWarning
            warnings.simplefilter("default", RuntimeWarning)
        return Table(metadata), combined

    def process_domeflat(self, ccd_bin, bias_frame=None, dark_frame=None):
        """Process the dome flat fields and return statistics

        [extended_summary]

        Parameters
        ----------
        ccd_bin : `str`, optional
            The binning to use for this routine [Default: None]
        bias_frame : `astropy.nddata.CCDData`, optional
            The combined, overscan-subtracted bias frame  [Default: None]
            If None, the routine will load in a saved bias
        dark_frame : `astropt.nddata.CCDData`, optional
            The combined, bias-subtracted dark frame [Default: None]
            If None, the routine will load in a saved dark, if necessary

        Returns
        -------
        `astropy.table.Table`
            The table of relevant metadata and statistics for each frame
        """
        # Check for existance of flats with this binning, else retun empty Table()
        domeflat_cl = self._check_ifc("domeflat_cl", ccd_bin)
        if not domeflat_cl.files:
            return Table()

        # Check for actual bias frame, else make something up
        if not bias_frame:
            msgs.info("No appropriate bias frames passed; loading saved BIAS...")
            bias_frame = utils.load_saved_bias(self.flags["instrument"], ccd_bin)
        else:
            # Write this bias to disk for future use
            utils.write_saved_bias(bias_frame, self.flags["instrument"], ccd_bin)

        if self.debug:
            msgs.info("Processing flat frames...")

        # Show progress bar for processing flat frames
        progress_bar = tqdm(
            total=len(domeflat_cl.files),
            unit="frame",
            unit_scale=False,
            colour="#fc6a03",
        )

        # Loop through flat frames, subtracting bias and gathering statistics
        metadata, coord_arrays = [], None
        for ccd, fname in domeflat_cl.ccds(bitpix=16, return_fname=True):

            hdr = ccd.header
            # Add a "short filename" to the header for use further along
            hdr["SHORT_FN"] = fname.split(os.sep)[-1]

            # Fit & subtract the overscan section, trim the image, subtract bias
            ccd = utils.trim_oscan(ccd, hdr["BIASSEC"], hdr["TRIMSEC"])
            ccd = ccdp.subtract_bias(ccd, bias_frame)

            # If a DARK frame was passed, scale and subtract
            if dark_frame:
                # NOTE: Not yet implemented
                pass

            # Work entirely in COUNT RATE -- ergo divide by exptime
            count_rate = ccd.divide(hdr["EXPTIME"])

            # Statistics, statistics, statistics!!!!
            quadsurf, coord_arrays = utils.fit_quadric_surface(count_rate, coord_arrays)

            metadict = base_metadata_dict(hdr, count_rate, quadsurf)

            # Additional fields for flats: Stuff that can block the light path
            #  Do type-forcing to make InfluxDB happy
            for rc_num in [1, 2]:
                for axis in ["x", "y"]:
                    metadict[f"rc{rc_num}pos_{axis.lower()}"] = float(
                        hdr[f"P{rc_num}{axis.upper()}"]
                    )
            metadict["icpos"] = float(hdr["ICPOS"])
            for axis in utils.LDT_FMS:
                metadict[f"fmpos_{axis.lower()}"] = float(hdr[f"FM{axis.upper()}POS"])

            metadata.append(metadict)
            progress_bar.update(1)

        progress_bar.close()

        # Convert the list of dicts into a Table and return
        return Table(metadata)

    def process_skyflat(self, ccd_bin, bias_frame=None, dark_frame=None):
        """Process the sky flat fields and return statistics

        NOTE: Not yet implemented --
            tqdm color should be "red"
        """
        [ccd_bin, bias_frame, dark_frame, self.debug]
        return Table()

    def _check_ifc(self, frametype, ccd_bin):
        """Check the IFC being processed

        This is a DRY block, used in both process_bias and process_flats.  It
        does the various checks for existance of files, and making sure binning
        is uniform and FULL FRAME.

        Parameters
        ----------
        frametype : `str`
            Frametype to pull from the frame_dict
        ccd_bin : `str`
            The binning to use for this routine

        Returns
        -------
        `ccdproc.ImageFileCollection`
            Filtered ImageFileCollection, ready for processing

        Raises
        ------
        InputError
            Raised if the binning is not set.
        """
        ifc = self.frame_dict[frametype]

        # Error checking for binning
        if not ccd_bin:
            raise utils.InputError("Binning not set.")

        # If IFC is empty already, just return it
        if not ifc.files:
            return ifc

        # Double-check that we're processing FULL FRAMEs of identical binning only
        return ifc.filter(ccdsum=ccd_bin, subarrno=0)


# Helper Functions (Alphabetical) ============================================#
def base_metadata_dict(hdr, data, quadsurf, crop=100):
    """base_metadata_dict Create the basic metadata dictionary

    [extended_summary]

    Parameters
    ----------
    hdr : `astropy.io.fits.Header`
        FITS header for this frame
    data : `numpy.ndarray` or `astropy.nddata.CCDData`
        FITS image data for this frame
    crop : `int`, optional
        Size of the border around the edge of the frame to crop off
        [Default: 100]

    Returns
    -------
    `dict`
        The base metadata dictionary
    """
    # Make things easier by creating a slice for cropping
    allslice = np.s_[:, :]
    cropslice = np.s_[crop:-crop, crop:-crop]
    human_readable = utils.compute_human_readable_surface(quadsurf)
    human_readable.pop("typ")
    shape = (hdr["naxis1"], hdr["naxis2"])

    # TODO: Add error checking here to keep InfluxDB happy -- Maybe this is enough?
    metadict = {
        "dateobs": f"{hdr['DATE-OBS'].strip()}",
        "instrument": f"{hdr['INSTRUME'].strip()}",
        "frametype": f"{hdr['OBSTYPE'].strip()}",
        "obserno": int(hdr["OBSERNO"]),
        "filename": f"{hdr['SHORT_FN'].strip()}",
        "binning": "x".join(hdr["CCDSUM"].split()),
        "filter": f"{hdr['FILTERS'].strip()}",
        "numamp": int(hdr["NUMAMP"]),
        "ampid": f"{hdr['AMPID'].strip()}",
        "exptime": float(hdr["EXPTIME"]),
        "mnttemp": float(hdr["MNTTEMP"]),
        "tempamb": float(hdr["TEMPAMB"]),
        "cropsize": int(crop),
    }
    for name, the_slice in zip(["frame", "crop"], [allslice, cropslice]):
        metadict[f"{name}_avg"] = np.mean(data[the_slice])
        metadict[f"{name}_med"] = np.ma.median(data[the_slice])
        metadict[f"{name}_std"] = np.std(data[the_slice])
    for key, val in human_readable.items():
        metadict[f"qs_{key}"] = val
    lin_flat, quad_flat = utils.compute_flatness(
        human_readable, shape, metadict["crop_std"]
    )
    metadict["lin_flat"] = lin_flat
    metadict["quad_flat"] = quad_flat

    # for i, m in enumerate(['b','x','y','xx','yy','xy']):
    #     metadict[f"qs_{m}"] = quadsurf[i]

    return metadict
