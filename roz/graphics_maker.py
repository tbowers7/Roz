# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 08-Oct-2021
#
#  @author: tbowers

"""Make graphs desired for inclusion on the Confluence page

This module is part of the Roz package, written at Lowell Observatory.

This module will eventually create graphs for investigative purposes and/or
for upload to Confluence.  Alternatively, what could be produced here could
also be produced by the Grafana interface with the InfluxDB database that will
house the actual data from Roz.

This module primarily trades in... hope?

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""

# Built-In Libraries
import pathlib

# 3rd Party Libraries
import astropy.nddata
import astropy.visualization
import matplotlib.pyplot as plt
import numpy as np

# Internal Imports
from roz import alerting
from roz import utils


def plot_lmi_bias_temp(bias_values, temp_values, binning=None):
    """Plot the LMI bias level versus temperature

    .. TODO::
        Make this function actually do something.

    """
    # Set up the plot environment
    _, axis = plt.subplots()
    tsz = 8

    # Check that the temperature values are sensible!
    idx = np.where((temp_values > -20) & (temp_values < 30))
    temp_values = temp_values[idx]
    bias_values = bias_values[idx]

    axis.plot(temp_values, bias_values, ".")
    axis.set_xlabel("Mount Temperature (ºC)", fontsize=tsz)
    axis.set_ylabel(f"LMI Mean Bias Level (ADU); Binning: {binning}", fontsize=tsz)

    axis.tick_params(
        "both", which="both", direction="in", top=True, right=True, labelsize=tsz
    )
    plt.tight_layout()
    plt.show()


def make_png_thumbnail(img_fn, inst_flags, latest=True, problem=False, debug=False):
    """Make PNG thumbnails of calibration frames

    These thumbnails will be uploaded to the Confluence page and will be
    linked to from the table created in Roz and uploaded.

    Parameters
    ----------
    img_fn : :obj:`str` or :obj:`pathlib.Path`
        The filename or path to the image for which a PNG will be created.
    inst_flags : dict
        The instrument flags dictionary.
    latest : bool, optional
        Label this image as a "Latest" image rather than a "Nominal" image.
        (Default: True)
    problem : bool, optional
        If True, this overrides the ``latest`` flag and labels it as a "Problem"
    debug : bool, optional
        Pring debugging statements?  (Default: False)

    Returns
    -------
    str
        The filename (without path) of the PNG created.
    """
    # Read in the image, with error checking
    try:
        ccd = astropy.nddata.CCDData.read(img_fn)
    except OSError as exception:
        alerting.send_alert("file_not_open", filename=img_fn, exception=exception)
        return None

    # Since we use the filename (sans path) in the graphic title...
    if isinstance(img_fn, str):
        img_fn = pathlib.Path(img_fn)
    img_fn = img_fn.name

    # Get the UT DATE -- forcing to be post-5pm MST
    ut_date = utils.scrub_isot_dateobs(ccd.header["DATE-OBS"], add_hours=3).strftime(
        "%Y%m%d"
    )

    # Construct the output filename from the image header
    png_fn = ".".join(
        [
            ccd.header["INSTRUME"].lower(),
            # TODO: Not strictly correct, if we want this routine to also make
            #       thumbnails of bais frames... needs thought.  For now...
            filt := ccd.header["FILTERS"] if inst_flags["get_flat"] else "",
            ut_date,
            f"{ccd.header['OBSERNO']:04d}",
            "png",
        ]
    )
    if debug:
        print(f"This is the PNG filename!  {png_fn}")

    # Set up the plot environment
    _, axis = plt.subplots(figsize=(5, 5.2))
    tsz = 8

    # Plotting percentile limits -- convert to image intensity limits
    vmin, vmax = get_image_intensity_limits(ccd)

    # Show the data on the plot, using the limits computed above
    axis.imshow(ccd.data, vmin=vmin, vmax=vmax, origin="lower", cmap="gist_gray")

    # Set the title and don't draw any axes
    title = [
        "*Problem*" if problem else "*Latest*" if latest else "*Nominal*",
        ccd.header["INSTRUME"].upper(),
        ccd.header["OBSTYPE"],
        ccd.header["CCDSUM"].strip().replace(" ", "x"),
        filt,
        f"{ccd.header['EXPTIME']}s",
        f"{ut_date}UT",
        img_fn,
    ]
    axis.set_title("   ".join(title), y=-0.00, pad=-14, fontsize=tsz)
    axis.axis("off")

    # Clean up the plot and save
    plt.tight_layout()
    plt.subplots_adjust(left=0.01, right=0.99, top=0.99)
    plt.savefig(utils.Paths.thumbnail.joinpath(png_fn))
    plt.close()

    # Return the filename we just saved
    return png_fn


def get_image_intensity_limits(ccd):
    """get_image_intensity_limits Return appropriate plot intensity limits

    Compute appropraite intensity ranges for plotting images based on the
    FITS header keyword ``OBSTYPE``.

    .. TODO::
        This function feels like it should be part of a larger Graphics class.

    Parameters
    ----------
    image : `astropy.nddata.CCDData`_
        The CCDData object for a frame

    Returns
    -------
    tuple
        The minimum and maximum values in the data image that correspond to
        the percentiles assigned by ``OBSTYPE``.
    """
    # Get the image type from the FITS header, and select the percentile range
    if (obstype := ccd.header.get("OBSTYPE")) == "OBJECT":
        pmin, pmax = 25, 99.75
    elif obstype in ["DOME FLAT", "SKY FLAT"]:
        pmin, pmax = 3, 99
    elif obstype == "BIAS":
        pmin, pmax = 5, 95
    else:
        pmin, pmax = 0, 100

    # Compute the iterval and return
    interval = astropy.visualization.AsymmetricPercentileInterval(
        pmin, pmax, n_samples=10000
    )
    return interval.get_limits(ccd.data)
