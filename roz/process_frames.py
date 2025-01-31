# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 23-Sep-2021
#
#  @author: tbowers

"""Process the Frames for One Night for specified instrument

This module is part of the Roz package, written at Lowell Observatory.

This module takes the gathered calibration/science frames from a night (as
collected by :obj:`~roz.gather_frames`) and performs basic data processing (bias &
overscan subtraction) before gathering statistics.  The statistics are then
stuffed into a database object (from :obj:`~roz.database_manager`) for later use.

Classes for Calibration, Science, and AllSky frames are included in this module.

This module primarily trades in AstroPy Table objects (`astropy.table.Table`_)
and CCDPROC Image File Collections (`ccdproc.ImageFileCollection`_), along with
the odd AstroPy CCDData object (`astropy.nddata.CCDData`_) and basic python
dictionaries (:obj:`dict`).

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""

# Built-In Libraries
import warnings

# 3rd Party Libraries
import astropy.stats
import astropy.table
import astropy.wcs
import ccdproc
import numpy as np
from tqdm import tqdm

# Internal Imports
from roz import gather_frames
from roz import msgs
from roz import utils


# Set API Components
__all__ = ["CalibContainer", "ScienceContainer", "AllSkyContainer"]

# Silence Superflous AstroPy FITS Header Warnings
warnings.simplefilter("ignore", astropy.wcs.FITSFixedWarning)


class _ContainerBase:
    """Base class for containing and processing Roz frames

    Parameters
    ----------
    directory : :obj:`pathlib.Path`
        Processing directory
    inst_flags : dict
        Dictionary of instrument flags from
        :func:`~roz.gather_frames.Dumbwaiter.set_instrument_flags()`
    debug : bool, optional
        Print debugging statements? (Default: True)
    mem_limit : float, optional
        Memory limit for the image combination routine (Default: 8.192e9 bytes)
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

        # Init other things
        self.frame_dict = {}

    def _check_ifc(self, frametype, config):
        """Check the IFC being processed

        This is a DRY block, used in both process_bias and process_flats.  It
        does the various checks for existance of files, and making sure binning
        is uniform and FULL FRAME.

        Parameters
        ----------
        frametype : str
            Frametype to pull from the frame_dict
        ccd_bin : str
            The binning to use for this routine
        amp_config : str
            The amplifier ID(s) to use for this routine

        Returns
        -------
        `ccdproc.ImageFileCollection`_
            Filtered ImageFileCollection, ready for processing

        """
        ccd_bin, amp_config = config
        ifc = self.frame_dict[frametype]

        # Error checking for binning
        if not ccd_bin:
            msgs.error("Binning not set.")
        if not amp_config:
            msgs.error("Amplifier configuration not set.")

        # If IFC is empty already, just return it
        if not ifc.files:
            return ifc

        kwargs = utils.parse_ampconfig(amp_config)

        # Double-check that we're processing FULL FRAMEs of identical config only
        return ifc.filter(ccdsum=ccd_bin, subarrno=0, **kwargs)

    @property
    def unique_detector_configs(self):
        """Returns the set of unique detector configurations

        Returns
        -------
        list
            List of unique detector configurations, expressed as tuples of
            ``(ccd_bin, amp_id)``
        """
        configs = []
        for ccd_bin in self.frame_dict.get("bin_list", ["1 1"]):
            amplist = [
                utils.parse_lois_ampids(hdr)
                for hdr in self.frame_dict["icl"].headers(ccdsum=ccd_bin)
            ]
            # Sorted list set to keep it identical between runs
            for amp in sorted(list(set(amplist))):
                configs.append((ccd_bin, amp))
        return configs

    def reset_config(self):
        """Reset the configuration-specific attributes to ``None``

        Loop through the instance attributes, and set all those ending in
        "_meta" or "_frame" to None.
        """
        for attr in dir(self):
            if attr.endswith("_meta") or attr.endswith("_frame"):
                setattr(self, attr, None)

    # General Helper Functions -- Static Methods =============================#
    @staticmethod
    def basemeta_dict(hdr, data, quadsurf, crop=100):
        """Create the basic metadata dictionary

        [extended_summary]

        Parameters
        ----------
        hdr : `astropy.io.fits.Header`_
            FITS header for this frame
        data : `numpy.ndarray`_
            FITS image data for this frame -- NOT `astropy.nddata.CCDData`_
        crop : int, optional
            Size of the border around the edge of the frame to crop off
            (Default: 100)

        Returns
        -------
        dict
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
            "ampid": utils.parse_lois_ampids(hdr),
            "exptime": float(hdr["EXPTIME"]),
            "mnttemp": float(hdr["MNTTEMP"]),
            "tempamb": float(hdr["TEMPAMB"]),
            "cropsize": int(crop),
        }
        # Use the np.nan[stat] functions in case of upstream NaN's.
        for name, the_slice in zip(["frame", "crop"], [allslice, cropslice]):
            metadict[f"{name}_avg"] = np.nanmean(data[the_slice])
            metadict[f"{name}_med"] = np.nanmedian(data[the_slice])
            metadict[f"{name}_std"] = np.nanstd(data[the_slice])
        for key, val in human_readable.items():
            metadict[f"qs_{key}"] = val
        lin_flat, quad_flat = utils.compute_flatness(
            human_readable, shape, metadict["crop_std"]
        )
        metadict["lin_flat"] = lin_flat
        metadict["quad_flat"] = quad_flat

        # Return!
        return metadict

    @staticmethod
    def load_saved_bias(instrument, config):
        """Load a saved (canned) bias frame

        In the event that a data set does not contain a concomitant bias frame(s),
        load in a saved (canned) frame for use with processing the flat frames.

        Parameters
        ----------
        instrument : str
            Instrument name from :obj:`instrument_flags()`
        config : tuple
            (Instrument binning from CCDSUM, AMPIDs)

        Returns
        -------
        `astropy.nddata.CCDData`_
            The (canned) combined, overscan-subtracted bias frame
            If no saved bias exists, return ``None``
        """
        # Split out the tuple
        ccd_bin, amp_id = config

        # Build bias filename
        fname = f"bias_{instrument.lower()}_{ccd_bin.replace(' ','x')}_{amp_id}.fits"

        # If the proper filename exists, read it in and return
        if utils.Paths.data.joinpath(fname).is_file():
            msgs.info(f"Reading in saved file {fname}...")
            return astropy.nddata.CCDData.read(utils.Paths.data.joinpath(fname))

        # If nothing exists, print a warning and return None
        msgs.warn(
            f"Saved BIAS not found for {instrument.upper()} with "
            f"{ccd_bin.replace(' ','x')} binning and amplifer "
            f"{amp_id}.{msgs.newline()}Skipping bias subraction!"
        )
        return None

    @staticmethod
    def write_saved_bias(ccd, instrument, config):
        """Write a saved (canned) bias frame

        Write a bias frame to disk for use with other nights' data that has
        no bias.

        Parameters
        ----------
        ccd : `astropy.nddata.CCDData`_
            The (canned) combined, overscan-subtracted bias frame to write
        instrument : str
            Instrument name from instrument_flags()
        config : tuple
            (Instrument binning from CCDSUM, AMPIDs)
        """
        # Split out the tuple
        ccd_bin, amp_id = config

        # Build bias filename
        fname = f"bias_{instrument.lower()}_{ccd_bin.replace(' ','x')}_{amp_id}.fits"
        ccd.write(utils.Paths.data.joinpath(fname), overwrite=True)


class CalibContainer(_ContainerBase):
    """Class for containing and processing calibration frames

    This container holds the gathered calibration frames in the processing
    directory, as well as the processing routines needed for the various types
    of frames.  The class holds the general information needed by all
    processing methods.

    Parameters
    ----------
    directory : :obj:`pathlib.Path`
        Processing directory
    inst_flags : dict
        Dictionary of instrument flags from
        :func:`~roz.gather_frames.Dumbwaiter.set_instrument_flags()`
    debug : bool, optional
        Print debugging statements? (Default: True)
    mem_limit : float, optional
        Memory limit for the image combination routine (Default: 8.192e9 bytes)
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        # Get the frame dictionary to be used
        self.frame_dict = gather_frames.gather_calibration_frames(
            self.directory, self.flags
        )

        # Set up the various calibration output attritubes
        self.bias_meta = None
        self.dark_meta = None
        self.flat_meta = None
        self.skyf_meta = None
        self.bias_frame = None
        self.dark_frame = None

    def process_bias(self, config, combine_method="average"):
        """Process and combine available bias frames

        [extended_summary]

        Parameters
        ----------
        ccd_bin : str, optional
            Binning of the CCD -- must be specified by the caller (Default: None)
        combine_method : str, optional
            Combination method to pass to `ccdproc.combine`_  (Default: average)

        Returns
        -------
        `astropy.table.Table`_
            A table containing information about the bias frames for analysis
        `astropy.nddata.CCDData`_ or NoneType
            The combined, overscan-subtracted bias frame (if
            ``produce_combined == True`` else None)
        """
        # Parse instance attributes into expected variables
        bias_cl = self._check_ifc("bias_cl", config)
        produce_combined = self.flags["get_flat"]

        if not bias_cl.files:
            return
        if self.debug:
            msgs.info("Processing bias frames...")

        # Show progress bar for processing bias frames
        progress_bar = tqdm(
            total=len(bias_cl.files), unit="frame", unit_scale=False, colour="#a3c4d4"
        )

        # Loop through files
        bias_fns, metadata, coord_arrays = [], [], None
        for ccd, fname in bias_cl.ccds(bitpix=16, return_fname=True):
            # Convert the filename into the full path
            fname = self.directory.joinpath(fname)

            hdr = ccd.header
            # For BIAS set header FILTERS keyword to "DARK"
            hdr["FILTERS"] = "DARK"
            hdr["SHORT_FN"] = fname.name
            data = ccd.data[
                ccdproc.utils.slices.slice_from_string(
                    hdr["TRIMSEC"], fits_convention=True
                )
            ]

            # Statistics, statistics, statistics!!!!
            quadsurf, coord_arrays = utils.fit_quadric_surface(
                data, coord_arrays, fit_quad=True
            )
            metadata.append(self.basemeta_dict(hdr, data, quadsurf))

            # Fit the overscan section, subtract it, then trim the image
            ccd = utils.wrap_trim_oscan(ccd)
            # Write back to file, update the progress bar and repeat!
            # NOTE: We don't keep the CCDData objects in memory because it is a
            #       bit of a memory leak, and the ccdproc.combine() method
            #       rejiggers the CCDData objects internally in a way that
            #       actually doubles the amount of memory used.
            ccd.write(fname, overwrite=True)
            bias_fns.append(fname)
            progress_bar.update(1)

        progress_bar.close()

        if not bias_fns:
            msgs.error(
                f"No unprocessed bias frames found in the processing{msgs.newline()}"
                f"directory.  Make sure a clean set of raw images are{msgs.newline()}"
                "imported for processing."
            )

        # Convert the list of dicts into a Table and return, plus combined bias
        combined = None
        if produce_combined:
            if self.debug:
                msgs.info(f"Doing {combine_method} combine of biases now...")
            # Silence RuntimeWarning issued related to means of empty slices
            warnings.simplefilter("ignore", RuntimeWarning)
            combined = ccdproc.combine(
                bias_fns,
                method=combine_method,
                sigma_clip=True,
                mem_limit=self.mem_limit,
                sigma_clip_dev_func=astropy.stats.mad_std,
            )
            # Reinstate RuntimeWarning
            warnings.simplefilter("default", RuntimeWarning)

        # For some reason, the combined bias sometimes is not completely finite
        if np.sum(np.isfinite(combined)) != combined.size:
            msgs.test(
                "Percentage of finite elements in combined bias: "
                f"{np.sum(np.isfinite(combined))/combined.size*100:.2f}%"
            )

        # Stuff into instance attributes
        self.bias_meta = astropy.table.Table(metadata)
        self.bias_frame = combined

    def process_dark(self, config, combine_method="average"):
        """Process and combine available dark frames

        .. note::
            Not yet implemented -- Boilerplate below is from process_bias

            ``tqdm`` color should be ``#736d67``
        """
        # Parse instance attributes into expected variables
        dark_cl = self._check_ifc("dark_cl", config)
        produce_combined = self.flags["get_flat"]

        if not dark_cl.files:
            return
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
            combined = ccdproc.combine(
                dark_ccds,
                method=combine_method,
                sigma_clip=True,
                mem_limit=self.mem_limit,
                sigma_clip_dev_func=astropy.stats.mad_std,
            )
            # Reinstate RuntimeWarning
            warnings.simplefilter("default", RuntimeWarning)

        # Stuff into instance attributes
        self.dark_meta = astropy.table.Table(metadata)
        self.dark_frame = combined

    def process_domeflat(self, config):
        """Process the dome flat fields and return statistics

        [extended_summary]

        Parameters
        ----------
        ccd_bin : str, optional
            The binning to use for this routine (Default: None)

        Returns
        -------
        `astropy.table.Table`_
            The table of relevant metadata and statistics for each frame
        """
        # Check for existance of flats with this binning, else retun empty Table()
        domeflat_cl = self._check_ifc("domeflat_cl", config)
        if not domeflat_cl.files:
            return

        # Check for actual bias frame, else make something up
        if not self.bias_frame:
            msgs.info("No bias frame(s) for this config; loading saved BIAS...")
            self.bias_frame = self.load_saved_bias(self.flags["instrument"], config)
        else:
            # Write this bias to disk for future use
            self.write_saved_bias(self.bias_frame, self.flags["instrument"], config)

        if self.debug:
            msgs.info("Processing dome flat frames...")

        # Show progress bar for processing flat frames ("Candlelight")
        progress_bar = tqdm(
            total=len(domeflat_cl.files),
            unit="frame",
            unit_scale=False,
            colour="#ffd21c",
        )

        # Loop through flat frames, subtracting bias and gathering statistics
        metadata, coord_arrays = [], None
        for ccd, fname in domeflat_cl.ccds(bitpix=16, return_fname=True):
            hdr = ccd.header
            # Add a "short filename" to the header for use further along
            hdr["SHORT_FN"] = fname

            # Fit & subtract the overscan section, trim the image.
            ccd = utils.wrap_trim_oscan(ccd)
            # If a bias exists, subtract it
            if self.bias_frame:
                ccd = ccdproc.subtract_bias(ccd, self.bias_frame)

            # If a DARK frame was passed, scale and subtract
            if self.dark_frame:
                # NOTE: Not yet implemented
                pass

            # Work entirely in COUNT RATE -- ergo divide by exptime
            count_rate = ccd.divide(hdr["EXPTIME"])

            # Statistics!  Pass only the DATA portion of the CCDData object
            quadsurf, coord_arrays = utils.fit_quadric_surface(
                count_rate.data, coord_arrays, fit_quad=True
            )
            metadict = self.basemeta_dict(hdr, count_rate.data, quadsurf)

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
        self.flat_meta = astropy.table.Table(metadata)

    def process_skyflat(self, config):
        """Process the sky flat fields and return statistics

        .. note::
            Not yet implemented -- Boilerplate below is from process_bias

            ``tqdm`` color should be ``#d8c3e1`` (skybluepink)
        """
        _, _ = config
        self.skyf_meta = astropy.table.Table()


class ScienceContainer(_ContainerBase):
    """Class for containing and processing science frames

    This container holds the gathered science frames in the processing
    directory, as well as the processing routines needed for the various types
    of frames.  The class holds the general information needed by all
    processing methods.

    Parameters
    ----------
    directory : :obj:`pathlib.Path`
        Processing directory
    inst_flags : dict
        Dictionary of instrument flags from
        :func:`~roz.gather_frames.Dumbwaiter.set_instrument_flags()`
    debug : bool, optional
        Print debugging statements? (Default: True)
    mem_limit : float, optional
        Memory limit for the image combination routine (Default: 8.192e9 bytes)
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        # Get the frame dictionary to be used
        self.frame_dict = gather_frames.gather_science_frames(
            self.directory, self.flags
        )

    def process_science(self, config):
        """Process the science frames

        _extended_summary_

        Parameters
        ----------
        config : tuple
            _description_
        """
        if self.debug:
            print(f"{config}")

    def process_standard(self, config):
        """Process the photometric standard frames

        _extended_summary_

        Parameters
        ----------
        config : tuple
            _description_
        """
        if self.debug:
            msgs.bug(f"{config}")


class AllSkyContainer(_ContainerBase):
    """Class for containing and processing All-Sky Camera frames

    This container holds the gathered all-sky frames in the processing
    directory, as well as the necessary processing routines.  The class holds
    the general information needed by all processing methods.

    Parameters
    ----------
    directory : :obj:`pathlib.Path`
        Processing directory
    inst_flags : dict
        Dictionary of instrument flags from
        :func:`~roz.gather_frames.Dumbwaiter.set_instrument_flags()`
    debug : bool, optional
        Print debugging statements? (Default: True)
    mem_limit : float, optional
        Memory limit for the image combination routine (Default: 8.192e9 bytes)
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        # Get the frame dictionary to be used
        self.frame_dict = gather_frames.gather_allsky_frames(self.directory, self.flags)

        # Set up the various allsky output attritubes
        self.allsky_meta = None

    def process_allsky(self, config):
        """Process the All-Sky Camera frames

        The All-Sky Cameras at both LDT and AM are OCULUS ALL-SKY
        CAMERA 180º by Starlight Xpress.

        The camera is built around a ICX267AL Sony SuperHAD interline CCD with
        low dark current and vertical anti-blooming.
        Pixel size: 4.65uM x 4.65uM, Image format: 1392 x 1040 pixels
        CCD Image area: 6.4mm (Horizontal) x 4.75mm (Vertical).

        The processing of these frames will include:

        1. Construction of an ALT/AZ coordinate system centered on the
           illuminated region of the CCD for masking.
        2. Identify the "Region of Interest" (e.g., EL >= ??) and compute
           statistics of the pixel values within the RoI (mean, median, stdev)
        3. Construct difference images between adjacent frames for the
           identification of clouds and other "bad" things in the sky.
        4. Compute statistics on the difference images.
        5. Place the relevant data into a ``meta_table`` for database storage.

        Parameters
        ----------
        config : tuple
            _description_
        """
        # Parse instance attributes into expected variables
        icl = self._check_ifc("icl", config)

        if not icl.files:
            return
        if self.debug:
            msgs.info("Processing All-Sky Camera frames...")

        if self.debug:
            msgs.bug(f"{config}")
        msgs.warn("Really, I don't know what I'm doing here...")

        msgs.table("Step 1: Mask Stuff; mask will depend on LDT vs AM ASC")
        msgs.table("Step 2: Take stats of 'good' area of the image")
        msgs.table("Step 3: Make a difference frame with the adjacent exposure")
        msgs.table("Step 4: Take stats of 'good' area of difference frame")
        msgs.table("Step 5: Place the relevant data into a meta_table")
