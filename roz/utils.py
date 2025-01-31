# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 08-Oct-2021
#
#  @author: tbowers

"""Utility Functions and Variables

This module is part of the Roz package, written at Lowell Observatory.

This module contains various utility routines and global variables from across
the package.

This module primarily trades in... utility?

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""

# Built-In Libraries
import datetime
from importlib import resources
import os
import pathlib

# 3rd Party Libraries
import astropy.io.fits
import astropy.modeling.models
import astropy.nddata
import astropy.table
import ccdproc
import matplotlib.pyplot as plt
import numpy as np

# Lowell Libraries
import ligmos

# Internal Imports


# Classes to hold useful information
class Paths:
    """Class that holds the various paths needed"""

    # Main data & config directories
    config = resources.files("roz") / "config"
    data = resources.files("roz") / "data"
    thumbnail = resources.files("roz") / "thumbnails"

    # Particular filenames needed by various routines
    xml_table = data / "lmi_filter_table.xml"
    ecsv_filters = data / "lmi_filter_table.ecsv"
    ecsv_sechead = data / "lmi_table_sechead.ecsv"
    html_table_fn = "lmi_filter_table.html"
    lmi_dyntable = data / "lmi_dynamic_filter.ecsv"
    local_html_table_fn = data / "lmi_filter_table.html"
    css_table = data / "lmi_filter_table.css"

    # DB Query Filename
    dbqueries = config / "dbqueries.conf"

    def __init__(self):
        pass


# List of Imager Filters (augment, as needed for other instruments)
FILTER_LIST = {
    "LMI": list(astropy.table.Table.read(Paths.ecsv_filters)["FITS Header Value"]),
    "DEVENY": ["OPEN"],
    "NIHTS": ["OPEN"],
    "ET1": ["U", "B", "V", "R", "I", "g'", "r'", "i'"],
    "ET2": ["B", "V", "R", "u'", "g'", "r'", "i'", "z'"],
    "JC": ["U", "B", "V", "R", "I"],
    "SDSS": ["u'", "g'", "r'", "i'", "z'"],
}

# Fold Mirror Names
LDT_FMS = ["A", "B", "C", "D"]


def parse_ampconfig(amp_config: str) -> dict:
    """Parse the amplifier configuration for LMI

    Parameters
    ----------
    amp_config : :obj:`str`
        The amplifier configuration used for a frame

    Returns
    -------
    :obj:`dict`
        The keyword dictionary that can be parsed by :obj:`~ccdproc.ImageFileCollection`
    """
    # Parse the `amp_config` into something the ImageFileCollection can filter
    if len(amp_config) == 1:
        kwargs = {"numamp": 1, "ampid": amp_config}
    else:
        kwargs = {"numamp": len(amp_config)}
        for amp in amp_config:
            # Use `ord()` to convert the letter into a number
            kwargs[f"ampid{ord(amp) - 64:02d}"] = amp
    return kwargs


def parse_lois_ampids(hdr: astropy.io.fits.Header) -> str:
    """Parse the LOIS amplifier IDs

    LOIS is particular about how it records which amplifiers are used to read
    out the CCD.  Most of the time, users will use a single amplifier, whose ID
    is recorded in the 'AMPID' FITS keyword.  If, however, more than one
    amplifier is used, 'AMPID' is not present, and the amplifier combination
    must be reconstructed from the present 'AMPIDnn' keywords.

    .. note::
        This routine was written for LOIS headers.  Data recorded with a
        different system may not have NUMAMP and AMPID keywords.  In this
        event, ``NUMAMP = 1`` and ``AMPID = "A"`` will be returned for
        compatibility with the remainder of the package.

    Parameters
    ----------
    hdr : :obj:`~astropy.io.fits.Header`
        The FITS header for which the amplifier IDs are to be parsed

    Returns
    -------
    :obj:`str`
        The amplifier designation(s) used
    """
    # Basic 1-amplifier case:
    if int(hdr.get("NUMAMP", 1)) == 1:
        return f"{hdr.get('AMPID', 'A').strip()}"

    # Else, parse out all of the "AMPIDnn" keywords, join and return
    return "".join([val.strip() for kwd, val in hdr.items() if "AMPID" in kwd])


def read_instrument_table() -> astropy.table.Table:
    """Read in the instrument table

    Returns
    -------
    :obj:`~astropy.table.Table`
        The instrument flags table
    """
    return astropy.table.Table.read(Paths.config.joinpath("instrument_flags.ecsv"))


def read_ligmos_conffiles(
    confname: str, conffile: str = "roz.conf"
) -> ligmos.utils.classes.baseTarget:
    """Read a configuration file using LIGMOS

    Having this as a separate function may be a bit of an overkill, but it
    makes it easier to keep the ligmos imports only in one place, and
    simplifies the code elsewhere.

    Parameters
    ----------
    confname : :obj:`str`
        Name of the table within the configuration file to parse
    conffile : :obj:`str`, optional
        Name of the configuration file to parse  (Default: "roz.conf")

    Returns
    -------
    :class:`~ligmos.utils.classes.baseTarget`
        An object with arrtibutes matching the keys in the associated
        configuration file.
    """

    # Create various augmented classes for Roz-specific configuration things
    class _AlertTarget(ligmos.utils.classes.baseTarget):
        """
        For roz.conf:[rozSetup]
        """

        def __init__(self):
            # Gather up the properties from the base class
            super().__init__()
            # New attributes
            self.slack_channel = None

    class _DatabaseTarget(ligmos.utils.classes.baseTarget):
        """
        For roz.conf:[databaseSetup] and roz.conf:[q_rozdata]
        """

        def __init__(self):
            # Gather up the properties from the base class
            super().__init__()
            # New attributes
            self.tablename = None
            self.metricname = None

    class _SetupTarget(ligmos.utils.classes.baseTarget):
        """
        For roz.conf:[rozSetup]
        """

        def __init__(self):
            # Gather up the properties from the base class
            super().__init__()
            # New attributes
            self.processing_dir = None
            self.coldstorage_dir = None

    class _FilterTarget(ligmos.utils.classes.baseTarget):
        """
        For roz.conf:[lmifilterSetup]
        """

        def __init__(self):
            # Gather up the properties from the base class
            super().__init__()
            # New attributes
            self.space = None
            self.page_title = None

    # Case out the class to use:
    if confname == "databaseSetup":
        ConfClass = _DatabaseTarget
    elif confname == "rozSetup":
        ConfClass = _SetupTarget
    elif confname == "lmifilterSetup":
        ConfClass = _FilterTarget
    elif confname == "q_rozdata":
        ConfClass = ligmos.utils.classes.databaseQuery
    elif confname == "alertSetup":
        ConfClass = _AlertTarget
    else:
        ConfClass = ligmos.utils.classes.baseTarget

    ligconf = ligmos.utils.confparsers.rawParser(Paths.config.joinpath(conffile))
    ligconf = ligmos.workers.confUtils.assignConf(
        ligconf[confname], ConfClass, backfill=True
    )
    return ligconf


def scrub_isot_dateobs(dt_str: str, add_hours: int = 0) -> datetime.datetime:
    """Scrub the input DATE-OBS for ingestion by datetime

    First of all, strict ISO 8601 format requires a 6-digit "microsecond" field
    as the fractional portion of the seconds.  In the DATE-OBS field, lois only
    prints two digits for the fractional second.  The main scrub here adds
    additional trailing zeroes to satifsy the formatting requirements of
    :obj:`datetime.datetime.fromisoformat`.

    While this yields happy results for the majority of cases, sometimes lois
    has roundoff abnormalities in the time string written to the DATE-OBS
    header keyword.  For example, "2020-01-30T13:17:010.00" was written, where
    the seconds have 3 digits -- presumably the seconds field was constructed
    with a leading zero because ``sec`` was < 10, but when rounded for printing
    yielded "10.00", producing a complete seconds field of "010.00".

    These kinds of abnormalities cause the standard python datetime parsers to
    freak out with ``ValueError`` s.  This function attempts to return the
    datetime directly, but then scrubs any values cause a ``ValueError``.

    The scrubbing consists of deconstructing the string into its components,
    then carefully reconstructing it into proper ISO 8601 format.

    Parameters
    ----------
    dt_str : :obj:`str`
        Input datetime string from the DATE-OBS header keyword
    add_hours : :obj:`int`
        Number of hours to add to the datetime  (Default: 0)

    Returns
    -------
    :obj:`~datetime.datetime`
        The datetime object corresponding to the DATE-OBS input string
    """
    # Clean all leading / trailing whitespace
    dt_str = dt_str.strip()
    try:
        # fromisoformat() expects a 6-digit microsecond field; append zeros
        if (n_micro := len(dt_str.split(".")[-1])) < 6:
            dt_str += "0" * (6 - n_micro)
        return datetime.datetime.fromisoformat(dt_str) + datetime.timedelta(
            hours=add_hours
        )
    except ValueError:
        # Split out all pieces of the datetime, and recompile
        date, time = dt_str.split("T")
        yea, mon, day = date.split("-")
        hou, mnt, sec = time.split(":")
        # Check if the seconds is exactly equal to 60... increment minute
        if sec == "60.00":
            sec = "00.00"
            if mnt != "59":
                mnt = int(mnt) + 1
            else:
                mnt = "00"
                if hou != "23":
                    hou = int(hou) + 1
                else:
                    hou = "00"
                    # If the edge cases go past here, go buy a lottery ticket!
                    day = int(day) + 1
        # Reconstitute the DATE-OBS string
        date = f"{int(yea):04d}-{int(mon):02d}-{int(day):02d}"
        time = f"{int(hou):02d}:{int(mnt):02d}:{float(sec):09.6f}"
        return datetime.datetime.fromisoformat(f"{date}T{time}") + datetime.timedelta(
            hours=add_hours
        )


def set_std_tickparams(axis: plt.axis, tsz: int | float):
    """Set standard tick parameters for a plot

    These are my own "standards", based on plots I used to make in IDL.

    Parameters
    ----------
    axis : :obj:`~matplotlib.pyplot.axis`
        PyPlot axis for whom the tick parameters must be set
    tsz : :obj:`int` or :obj:`float`
        TypeSiZe
    """
    axis.tick_params(
        axis="both",
        which="both",
        direction="in",
        top=True,
        right=True,
        labelsize=tsz,
    )


def subpath(path_to_dir: str | pathlib.Path) -> str:
    """Simple function to return the instrument/date part of the path

    The instrument/date part of the path should be independent of machine
    on which Roz is running, so the notes posted to Slack, etc., will neither
    reveal the host machine nor will include that distracting information.

    Parameters
    ----------
    path_to_dir : :obj:`str` or :obj:`~pathlib.Path`
        The full path to the directory of interest

    Returns
    -------
    :obj:`str`
        The ``instrument/date`` portion of the full path
    """
    if isinstance(path_to_dir, str):
        path_to_dir = pathlib.Path(path_to_dir)
    return os.sep.join(path_to_dir.parts[-2:])


def table_sort_on_list(
    table: astropy.table.Table, colname: str, sort_list: list
) -> astropy.table.Table:
    """Sort an AstroPy Table according to a list

    The actual sorting of the table is code taken directly from `Astropy v5.0
    <https://docs.astropy.org/en/stable/_modules/astropy/table/table.html>`_.
    This function does an arbitrary sort based on an input list.

    Parameters
    ----------
    table : :obj:`~astropy.table.Table`
        The table to sort
    colname : :obj:`str`
        The column name to sort on
    sort_list : :obj:`list`
        The list of values to sort with such that ``table[colname] == sort_list``

    Returns
    -------
    :obj:`~astropy.table.Table`
        The sorted table

    Raises
    ------
    TypeError
        If the input table is not really a table
    ValueError
        If the ``sort_list`` is not the same length as the table
    """
    # Check that the input parameters are of the proper type
    if not isinstance(table, astropy.table.Table):
        raise TypeError(
            "table must be of type astropy.table.Table not " f"{type(table)}"
        )
    sort_list = list(sort_list)

    # Check that `sort_list` is the same length as the table
    if len(sort_list) != len(table[colname]):
        raise ValueError(
            f"Sorting list and table column {colname} " "must be the same length."
        )

    # Find the indices that sort the table by sort_list
    table.add_index(colname)
    indices = []
    for sort_item in sort_list:
        indices.append(table.loc_indices[sort_item])

    # NOTE: This code paraphrased directly from astropy.table.table.py (v5.0)
    with table.index_mode("freeze"):
        for _, col in table.columns.items():
            # Make a new sorted column.
            new_col = col.take(indices, axis=0)
            # Do the substitution
            try:
                col[:] = new_col
            except Exception:
                table[col.info.name] = new_col

    return table


def trim_oscan(
    ccd: astropy.nddata.CCDData, biassec: str, trimsec: str
) -> astropy.nddata.CCDData:
    """Subtract the overscan region and trim image to desired size

    The function `ccdproc.subtract_overscan`_ expects the TRIMSEC of the image
    (the part you want to keep) to span the entirety of one dimension, with the
    BIASSEC (overscan section) being at the end of the other dimension.
    Both LMI and DeVeny have edge effects on all sides of their respective
    chips, and so the TRIMSEC and BIASSEC do not meet the expectations of
    `ccdproc.subtract_overscan`_.

    Therefore, this function is a wrapper to first remove the undesired ROWS
    from top and bottom, then perform the `ccdproc.subtract_overscan`_ fitting
    and subtraction, followed by trimming off the now-spent overscan region.

    At present, the overscan region is modeled with a first-order Chebyshev
    one-dimensional polynomial.  The model used can be changed in the future
    or allowed as a input, as desired.

    .. note::

        This function explicitly assumes that the chip is read out ROW-by-ROW
        and that overscan pixels are in each ROW.  Some instruments may have
        the native orientation such that the CCD is read out along COLUMNS;
        if such an instrument is added to this package, the present routine
        will need to be modified to include a rotation such that the order
        of operations below is correctly applied to such data.

    Parameters
    ----------
    ccd : :obj:`~astropy.nddata.CCDData`
        The CCDData object upon which to operate
    biassec : :obj:`str`
        String containing the FITS-convention overscan section coordinates
    trimsec : :obj:`str`
        String containing the FITS-convention data section coordinates

    Returns
    -------
    :obj:`~astropy.nddata.CCDData`
        The properly trimmed and overscan-subtracted CCDData object
    """
    # Convert the FITS bias & trim sections into slice classes for use
    _, x_b = ccdproc.utils.slices.slice_from_string(biassec, fits_convention=True)
    y_t, x_t = ccdproc.utils.slices.slice_from_string(trimsec, fits_convention=True)

    # First trim off the top & bottom rows
    ccd = ccdproc.trim_image(ccd[y_t.start : y_t.stop, :])

    # Model & Subtract the overscan
    ccd = ccdproc.subtract_overscan(
        ccd,
        overscan=ccd[:, x_b.start : x_b.stop],
        median=True,
        model=astropy.modeling.models.Chebyshev1D(1),
    )

    # Trim the overscan & return
    return ccdproc.trim_image(ccd[:, x_t.start : x_t.stop])


def two_sigfig(value: float) -> str:
    """String representation of a float at 2 significant figures

    Simple utility function to return a 2-sigfig representation of a float.

    .. note::

        There is a limitation at present that at most 2 decimal places are
        shown.  Therefore, this function will not work as expected for
        values < 0.1

        If I can figure out dynamic format specifiers, this limitation
        can be removed.

        Also, zero is represented as ``-----`` rather than numerically.

    Parameters
    ----------
    value : :obj:`float`
        Input value to be stringified

    Returns
    -------
    :obj:`str`
        String representation of ``value`` at two significant figures
    """
    # If zero, return a 'N/A' type string
    if value <= 0 or isinstance(value, np.ma.core.MaskedConstant):
        return "-----"
    # Compute the number of decimal places using the log10.  The way
    #  np.around() works is that +decimal is to the RIGHT, hence the
    #  negative sign on log10.  The "+1" gives the second sig fig.
    try:
        decimal = -int(np.floor(np.log10(value))) + 1
    except ValueError:
        decimal = 0

    # Choose the output specification
    if decimal <= 0:
        return f"{np.around(value, decimals=decimal):.0f}"
    if decimal == 1:
        return f"{np.around(value, decimals=decimal):.1f}"
    return f"{np.around(value, decimals=decimal):.2f}"


def wrap_trim_oscan(ccd: astropy.nddata.CCDData) -> astropy.nddata.CCDData:
    """Wrap the :func:`roz.utils.trim_oscan()` function to handle multiple amps

    This function will perform the magic of stitching together multi-amplifier
    reads.  There may be instrument-specific issues related to this, but it is
    likely that only LMI will ever bet read out in multi-amplifier mode.

    .. note::
        TODO: Whether here or somewhere else, should convert things to electrons
        via the GAIN.  Might not be necessary within the context of Roz, but
        will be necessary for science frame analysis with multiple amplifier
        reads.

    Parameters
    ----------
    ccd : :obj:`~astropy.nddata.CCDData`
        The CCDData object upon which to operate

    Returns
    -------
    :obj:`~astropy.nddata.CCDData`
        The properly trimmed and overscan-subtracted CCDData object
    """
    # Shorthand
    hdr = ccd.header

    # The "usual" case, pass-through from `trim_oscan()`
    if hdr["NUMAMP"] == 1:
        return trim_oscan(ccd, hdr["BIASSEC"], hdr["TRIMSEC"])

    # Use the individual amplifier BIAS and TRIM sections to process
    amp_nums = [kwd[-2:] for kwd in hdr.keys() if "AMPID" in kwd]
    for amp_num in amp_nums:
        yrange, xrange = ccdproc.utils.slices.slice_from_string(
            hdr[f"TRIM{amp_num}"], fits_convention=True
        )
        ccd.data[yrange.start : yrange.stop, xrange.start : xrange.stop] = trim_oscan(
            ccd, hdr[f"BIAS{amp_num}"], hdr[f"TRIM{amp_num}"]
        ).data

    # Return the final trimmed image
    ytrim, xtrim = ccdproc.utils.slices.slice_from_string(
        hdr["TRIMSEC"], fits_convention=True
    )
    return ccdproc.trim_image(ccd[ytrim.start : ytrim.stop, xtrim.start : xtrim.stop])


# Quadric Surface Functions ==================================================#
def fit_quadric_surface(data, c_arr=None, fit_quad=True, return_surface=False):
    """Fit a quadric surface to an image array

    Performs a **LEAST SQUARES FIT** of a (plane or) quadric surface to an
    input image array.  The basic equation is::

            matrix ## fit_coeff = right_hand_side

    In specific, the quadric surface fit is either an elliptic or hyperbolic
    paraboloid (of arbitrary orientation), since the resulting equation is::

        z = a0 + a1•x + a2•y + a3•x^2 + a4•y^2 + a5•xy

    https://en.wikipedia.org/wiki/Quadric
    https://en.wikipedia.org/wiki/Paraboloid

    This routine computes the `matrix` needed, as well as the right_hand_side.
    The fit coefficients are found by miltiplying matrix^(-1) by the RHS.

    Fit coefficients are::

        coeff[0] = Baseline offset
        coeff[1] = Linear term in x
        coeff[2] = Linear term in y
        coeff[3] = Quadratic term in x
        coeff[4] = Quadratic term in y
        coeff[5] = Quadratic cross-term in xy

    where the last three are only fit and nonzero if ``fit_quad == True``

    .. note::

        To deal with possible NaN's in the input ``data``, use `numpy.nansum`_
        in place of `numpy.sum`_ when building the RHS of the matrix equation.

    To illustrate where power is emphasized in the LINEAR vs QUADRATIC fits,
    consider the matrices associated with the two cases for a 2x2 binned LMI
    array size:

    LINEAR matrix::

        9.4e+06 -4.7e+06 -4.7e+06     0       0       0
        -4.7e+06  7.4e+12  2.4e+06     0       0       0
        -4.7e+06  2.4e+06  7.4e+12     0       0       0
        0        0        0         1       0       0
        0        0        0         0       1       0
        0        0        0         0       0       1

    LINEAR inverse matrix::

        1.1e-07  6.7e-14  6.8e-14     0       0       0
        6.7e-14  1.3e-13    0         0       0       0
        6.8e-14    0      1.4e-13     0       0       0
        0        0        0         1       0       0
        0        0        0         0       1       0
        0        0        0         0       0       1

    Here, the linear terms in X and Y are independent of each other and depend
    only on themselves and the total data sum.

    For the quadratic case, the linear terms are largely the same, but there is
    some power slosh into/out of the quadratic terms:

    QUADRATIC matrix::

        9.4e+06 -4.7e+06 -4.7e+06  7.4e+12  7.4e+12  2.4e+06
        -4.7e+06  7.4e+12  2.4e+06 -1.1e+13 -3.7e+12 -3.7e+12
        -4.7e+06  2.4e+06  7.4e+12 -3.7e+12 -1.1e+13 -3.7e+12
        7.4e+12 -1.1e+13 -3.7e+12  1.1e+19  5.8e+18  5.6e+12
        7.4e+12 -3.7e+12 -1.1e+13  5.8e+18  1.0e+19  5.5e+12
        2.4e+06 -3.7e+12 -3.7e+12  5.6e+12  5.5e+12  5.8e+18

    QUADRATIC inverse matrix::

        3.7e-07 -1.0e-13 -1.0e-13 -1.7e-13 -1.7e-13  4.3e-20
        -1.0e-13  1.3e-13  4.3e-20  2.1e-19  1.1e-34  8.6e-20
        -1.0e-13  4.3e-20  1.4e-13 -7.1e-33  2.2e-19  8.6e-20
        -1.7e-13  2.1e-19  1.5e-29  2.1e-19  8.5e-35  3.6e-35
        -1.7e-13  1.4e-34  2.2e-19  1.4e-34  2.2e-19 -8.7e-36
        4.3e-20  8.6e-20  8.6e-20  5.3e-36 -2.7e-41  1.7e-19

    Parameters
    ----------
    data : `numpy.ndarray`_
        The image (as a 2D array) to be fit with a surface
    ca : dict, optional
        Dictionary of coefficient arrays needed for creating the matrix
        (Default: None)
    fit_quad : bool, optional
        Fit a quadric surface, rather than a plane, to the data (Default: True)
    return_surface : bool, optional
        Return the model surface, built up from the fit coefficients?
        (Default: False)

    Returns
    -------
    `numpy.ndarray`_
        Array of 3 (plane) or 6 (quadric surface) fit coefficients
    dict
        Dictionary of coefficient arrays needed for creating the matrix
    `numpy.ndarray`_ (if `return_surface == True`)
        The 2D array modeling the surface ensconced in the first return.  Array
        is of same size as the input `data`.
    """
    # Construct the matrix for use with the LEAST SQUARES FIT
    #  np.dot(mat, fitvec) = RHS

    # Set up the matrix as an indentity matrix, and fill in the portions needed
    n_terms = 6
    matrix = np.identity(n_terms)

    # Produce the coordinate arrays, if not fed an existing dict OR if the
    #  array size is different (occasional edge case)
    reproduce = (not c_arr) or (data.shape != c_arr["x_coord_arr"].shape)
    c_arr = produce_coordinate_arrays(data.shape) if reproduce else c_arr

    # Fill in the matrix elements
    #  Upper left quadrant (or only quadrant, if fitting linear):
    matrix[:3, :3] = [
        [c_arr["n_pixels"], c_arr["sum_x"], c_arr["sum_y"]],
        [c_arr["sum_x"], c_arr["sum_x2"], c_arr["sum_xy"]],
        [c_arr["sum_y"], c_arr["sum_xy"], c_arr["sum_y2"]],
    ]

    # And the other 3 quadrants, if fitting a quadric surface
    if fit_quad:
        # Lower left quadrant:
        matrix[3:, :3] = [
            [c_arr["sum_x2"], c_arr["sum_x3"], c_arr["sum_x2y"]],
            [c_arr["sum_y2"], c_arr["sum_xy2"], c_arr["sum_y3"]],
            [c_arr["sum_xy"], c_arr["sum_x2y"], c_arr["sum_xy2"]],
        ]
        # Right half:
        matrix[:, 3:] = [
            [c_arr["sum_x2"], c_arr["sum_y2"], c_arr["sum_xy"]],
            [c_arr["sum_x3"], c_arr["sum_xy2"], c_arr["sum_x2y"]],
            [c_arr["sum_x2y"], c_arr["sum_y3"], c_arr["sum_xy2"]],
            [c_arr["sum_x4"], c_arr["sum_x2y2"], c_arr["sum_x3y"]],
            [c_arr["sum_x2y2"], c_arr["sum_y4"], c_arr["sum_xy3"]],
            [c_arr["sum_x3y"], c_arr["sum_xy3"], c_arr["sum_x2y2"]],
        ]

    # The right-hand side of the matrix equation (start as zeros, fill in):
    right_hand_side = np.zeros(n_terms)

    # Top half:
    right_hand_side[:3] = [
        np.nansum(data),
        np.nansum(x_d := np.multiply(c_arr["x_coord_arr"], data)),
        np.nansum(y_d := np.multiply(c_arr["y_coord_arr"], data)),
    ]

    if fit_quad:
        # Bottom half:
        right_hand_side[3:] = [
            np.nansum(np.multiply(c_arr["x_coord_arr"], x_d)),
            np.nansum(np.multiply(c_arr["y_coord_arr"], y_d)),
            np.nansum(np.multiply(c_arr["x_coord_arr"], y_d)),
        ]

    # Here's where the magic of matrix multiplication happens!
    fit_coefficients = np.dot(np.linalg.inv(matrix), right_hand_side)

    # If not returning the model surface, go ahead and return now
    if not return_surface:
        return fit_coefficients, c_arr

    # Build the model fit from the coefficients
    model_fit = (
        fit_coefficients[0]
        + fit_coefficients[1] * c_arr["x_coord_arr"]
        + fit_coefficients[2] * c_arr["y_coord_arr"]
    )

    if fit_quad:
        model_fit += (
            fit_coefficients[3] * c_arr["x2"]
            + fit_coefficients[4] * c_arr["y2"]
            + fit_coefficients[5] * c_arr["xy"]
        )

    return fit_coefficients, c_arr, model_fit


def produce_coordinate_arrays(shape):
    """Produce the dictionary of coordinate arrays

    Since these coordinate arrays are dependent ONLY upon the SHAPE of the
    input array, when doing multiple fits of data arrays with the same size, it
    greatly speeds things up to compute these arrays once and reuse them.

    Parameters
    ----------
    shape : tuple
        The ``.shape`` of the data (`numpy.ndarray`_)

    Returns
    -------
    dict
        Dictionary of coefficient arrays needed for creating the matrix
    """
    # Construct the arrays for doing the matrix magic -- origin in center
    n_y, n_x = shape
    x_arr = np.tile(np.arange(n_x), (n_y, 1)) - (n_x / 2.0)
    y_arr = np.transpose(np.tile(np.arange(n_y), (n_x, 1))) - (n_y / 2.0)

    # Compute the terms needed for the matrix
    return {
        "n_x": n_x,
        "n_y": n_y,
        "x_coord_arr": x_arr,
        "y_coord_arr": y_arr,
        "n_pixels": x_arr.size,
        "sum_x": np.sum(x_arr),
        "sum_y": np.sum(y_arr),
        "sum_x2": np.sum(x_2 := np.multiply(x_arr, x_arr)),
        "sum_xy": np.sum(x_y := np.multiply(x_arr, y_arr)),
        "sum_y2": np.sum(y_2 := np.multiply(y_arr, y_arr)),
        "sum_x3": np.sum(np.multiply(x_2, x_arr)),
        "sum_x2y": np.sum(np.multiply(x_2, y_arr)),
        "sum_xy2": np.sum(np.multiply(x_arr, y_2)),
        "sum_y3": np.sum(np.multiply(y_2, y_arr)),
        "sum_x4": np.sum(np.multiply(x_2, x_2)),
        "sum_x3y": np.sum(np.multiply(x_2, x_y)),
        "sum_x2y2": np.sum(np.multiply(x_2, y_2)),
        "sum_xy3": np.sum(np.multiply(x_y, y_2)),
        "sum_y4": np.sum(np.multiply(y_2, y_2)),
        "x2": x_2,
        "xy": x_y,
        "y2": y_2,
    }


def compute_human_readable_surface(coefficients):
    """Rotate a quadric surface surface into standard-ish form

    Use the standard form of::

        z = Ax^2 + Bxy + Cy^ + Dx + Ey + F

    Find the rotation when the axes of the surface are along x' and y'::

        x = x'*cos(th) - y'*sin(th)
        y = x'*sin(th) + y'*cos(th)

    Rotate this into standard form of::

        z = (a')x'^2 + (b')y'^2 + (c')x' + (d')y' + F

    where::

          a' = quadratic coefficient along x'
          b' = quadratic coefficient along y'
          c' = slope along x'
          d' = slope along y'

    https://courses.lumenlearning.com/ivytech-collegealgebra/chapter/writing-equations-of-rotated-conics-in-standard-form/

    Parameters
    ----------
    coefficients : `numpy.ndarray`_
        Coefficients output from :func:`~roz.utils.fit_quadric_surface`

    Returns
    -------
    dict
        Dictionary of human-readable quantities
    """
    # Parse the coefficients from the quadric surface into standard form
    F, D, E, A, C, B = coefficients

    # Compute the rotation of the axes of the surface away from x-y
    theta = 0.5 * np.arctan2(B, A - C)

    # Use a WHILE loop to check for orientation issues
    good_orient = False
    while not good_orient:
        # Always use a theta between 0º and 180º:
        theta = theta + np.pi if theta < 0 else theta
        theta = theta - np.pi if theta > np.pi else theta

        # Define sine and cosine for ease of typing and reading
        costh = np.cos(theta)
        sinth = np.sin(theta)

        # Compute the rotated coefficients
        #  a_prime == coefficient on x'^2 in Standard Form
        #  b_prime == coefficient on y'^2 in Standard Form
        a_prime = A * costh**2 + B * sinth * costh + C * sinth**2
        b_prime = A * sinth**2 - B * sinth * costh + C * costh**2

        # Check orientation
        #   (x' corresponds to the "semimajor" axis of an ellipse)
        if np.abs(a_prime) <= np.abs(b_prime):
            good_orient = True
        elif all(np.isfinite([a_prime, b_prime])):
            theta += np.pi / 2.0
        else:
            # If either of the above is non-finite, just move on.
            break

    # Along the native axes, the coefficient on x'y' == 0
    #  Compute as a check
    # xpyp = 2*(C-A)*sinth*costh + B*(costh**2 - sinth**2)

    # Convert values into human-readable things
    return {
        "rot": np.rad2deg(theta),
        "maj": a_prime,
        "min": b_prime,
        "bma": D * costh + E * sinth,
        "bmi": -D * sinth + E * costh,
        "zpt": F,
        "open": int(np.sign(a_prime)) if np.sign(a_prime) == np.sign(b_prime) else 0,
        "typ": "Plane"
        if a_prime == 0 and b_prime == 0
        else f"Elliptic Paraboloid {'Up' if np.sign(a_prime) == 1 else 'Down'}"
        if np.sign(a_prime) == np.sign(b_prime)
        else "Hyperbolic Paraboloid",
    }


def compute_flatness(human, shape, stddev):
    """Compute "flatness" statistics

    This function computes a pair of "flatness" statistics for calibration
    frames.  These are used as both a measure in themselves and as a marker
    for investigating change over time.  Changes in "flatness" are used as one
    of the alerting criteria.

    The statistics are basically computed as how fast the large-scale shape
    changes compared to both the smaller dimension of the image and the
    variability within the image, as defined by the "cropped" standard
    deviation.

    For each of the linear (plane) and quadratic portions of the quadric
    surface fit, the flatness statistic is computed as:

    .. code-block ::

        flatness = (smaller dimension, pix) / (change scale, pix per ADU) /
                   (standard deviation, ADU)

    The resulting statistic is unitless, and always positive.  A value of 1
    implies that the fit surface changes by 1 standard deviation over the
    length of the image's smaller dimension.  A perfectly flat image would
    have a value of zero -- highly curved or tilted images would have values
    much larger than 1.

    Parameters
    ----------
    human : dict
        Dictionary of human-readable quantities from
        :func:`~roz.utils/compute_human_readable_surface`
    shape : tuple
        Tuple of frame sizes (nx, ny)
    stddev : float
        Standard deviation of the "crop" section of the frame, used as a scale
        aganist which the tilt or curvature nonflatness is measured.

    Returns
    -------
    lin_flat : float
        Linear flatness statistic
    quad_flat : float
        Quadratic flatness statistic
    """
    # Frame minimum dimension
    npix_min = np.minimum(shape[0], shape[1])

    # The keys 'bma', 'bmin' refer to the linear slopes of the fit surface,
    #  which have a meaning of change in value per pixel.  Dividing the slope
    #  by the image standard deviation yields a change in value scaled to the
    #  STDDEV per pixel.  Dividing the number of pixels in the narrow dimension
    #  of the detector by this scaled slope yields a linear flatness metric
    #  that has meaning of "# sigma change in value across the narrow range
    #  of the detector".  We use the steeper slope of the two returned.
    lin_flat = (
        npix_min * np.maximum(np.abs(human["bma"]), np.abs(human["bmi"])) / stddev
    )

    # The keys 'maj' and 'min' refer to the quadratic coefficients of the fit
    #  surface, which have a meaning of something like the change in value per
    #  pixel^2.  Using the same logic as above...
    quad_flat = (
        npix_min**2 * np.maximum(np.abs(human["maj"]), np.abs(human["min"])) / stddev
    )

    return lin_flat, quad_flat
