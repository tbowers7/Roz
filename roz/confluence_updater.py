# -*- coding: utf-8 -*-
#
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
#  Created on 23-Sep-2021
#
#  @author: tbowers

"""Update the Confluence (or other) Webpage with Latest Stats

This module is part of the Roz package, written at Lowell Observatory.

This module takes the database objects produces elsewhere and prepares updated
content tables for upload to Confluence.  The only function herein that should
be called directly is update_filter_characterization().

Confluence API Documentation:
        https://atlassian-python-api.readthedocs.io/index.html

This module primarily trades in internal databse objects
(`roz.database_manager.CalibrationDatabase`).
"""

# Built-In Libraries
import datetime as dt
import os

# 3rd Party Libraries
from astropy.io.votable import parse as vo_parse
from astropy.table import hstack, Column, Table
from atlassian import Confluence
from bs4 import BeautifulSoup
import numpy as np
from numpy.ma.core import MaskedConstant

# Lowell Libraries
from ligmos import utils as lig_utils, workers as lig_workers

# Internal Imports
from .send_alerts import send_alert, ConfluenceAlert
from .utils import (
    two_sigfig,
    HTML_TABLE_FN,
    ECSV_FILTERS,
    ECSV_SECHEAD,
    LMI_FILTERS,
    LMI_DYNTABLE,
    ROZ_CONFIG,
    ROZ_DATA,
    XML_TABLE
)


def update_filter_characterization(database, delete_existing=False):
    """update_filter_characterization Update the Confluence Page

    This routine is the main function in this module, and should be the only
    one called directly.  It updates the Confluence page for LMI Filter
    Characterization.

    Parameters
    ----------
    database : `roz.database_manager.CalibrationDatabase`
        The database of calibration frames
    delete_existing : `bool`, optional
        Delete the existing table on Confluence before upoading the new one?
        [Defualt: True]  NOTE: Once in production, maybe turn this to False?
    """
    # Instantiate the Confluence communication class and read in SPACE and TITLE
    confluence, space, title = setup_confluence()

    # If the page doesn't already exist, send alert and return
    if not confluence.page_exists(space, title):
        send_alert(ConfluenceAlert)
        return

    # Update the HTML table attached to the Confluence page
    local_filename = ROZ_DATA.joinpath(HTML_TABLE_FN)
    success = update_lmi_filter_table(local_filename, database)

    # Get the `page_id` needed for intracting with the page we want to update
    page_id = confluence.get_page_id(space, title)

    # Remove the attachment on the Confluence page before uploading the new one
    # TODO: Need to decide if this step is necessary IN PRODUCTION -- maybe no?
    if delete_existing:
        confluence.delete_attachment(page_id, HTML_TABLE_FN)

    # Attach the HTML file to the Confluence page
    confluence.attach_file(local_filename, name=HTML_TABLE_FN, page_id=page_id,
                           content_type='text/html',
                           comment='LMI Filter Information Table')


def update_lmi_filter_table(filename, database, debug=True):
    """update_lmi_filter_table Update the LMI Filter Information Table

    Updates the HTML table of LMI filter information for upload to Confluence.
    This table is partially static (basic information about the filters, etc.),
    and partially dynamic, listing the UT date of the last flatfield, and the
    most recent estimation of countrate for that filter/lamp combination.

    This table also holds (links to) PNG images of 1) a carefully curated
    nominal flatfield, and 2) the most recent flatfield in this filter.

    Parameters
    ----------
    filename : `string`
        Local filename of the HTML table to create.
    database : `roz.database_manager.CalibrationDatabase`
        The database of calibration frames
    debug : `bool`, optional
        Print debugging statements? [Default: True]

    Returns
    -------
    `int`
        Success/failure bit
    """
    # Get the base (static) table
    lmi_filt, section_head = read_lmi_static_table()

    # Use the `database` to create the dynamic portions of the LMI table
    lmi_filt = construct_lmi_dynamic_table(lmi_filt, database)
    if debug:
        lmi_filt.pprint()

    # Use the AstroPy Table `lmi_filt` to construct the HTML table
    construct_lmi_html_table(lmi_filt, section_head, filename, debug=debug)

    # Return value -- 0 is success!
    return 0


def construct_lmi_dynamic_table(lmi_filt, database):
    """construct_lmi_dynamic_table Construct the dynamic portions of the table

    This function augments the static table (from the XML file) with dynamic
    information contained in the `database`.

    It should be noted that the Count Rate and Exptime columns are generated
    solely from the last night's flats.  So, if something funny was going on
    for those frames, the quoted values for these columns will be incorrect
    until closer-to-nominal flats are collected again.

    Parameters
    ----------
    lmi_filt : `astropy.table.Table`
        The AstroPy Table representation of the static portions of the LMI
        Filter Information table
    database : `roz.database_manager.CalibrationDatabase`
        The database of calibration frames

    Returns
    -------
    `astropy.table.Table`
        The dynamically augmented LMI Filter Information Table
    """
    # Check if the dynamic FITS table is extant
    if os.path.isfile(LMI_DYNTABLE):
        # Read it in!
        dyntable = Table.read(LMI_DYNTABLE)
    else:
        # Make a blank table
        nrow = len(LMI_FILTERS)
        col1 = Column(name='Latest Image', length=nrow, dtype='U128')
        col2 = Column(name='UT Date of Last Flat', length=nrow, dtype='U128')
        col3 = Column(name='Count Rate (ADU/s)', length=nrow, dtype=float)
        col4 = Column(name='Exptime for 20k cts (s)', length=nrow, dtype=float)
        dyntable = Table([col1, col2, col3, col4])

   # Loop through the filters, updating the relevant columns of the table
    for i,filt in enumerate(LMI_FILTERS):
        # Skip filters not used in this data set
        if database.flat[filt] is None:
            continue

        # But, only update if the DATOBS of this flat is LATER than what's
        #  already in the table.  If OLD > NEW, skip.
        new_date = database.flat[filt]['dateobs'][-1].split('T')[0]
        if ( existing_date := dyntable['UT Date of Last Flat'][i].strip() ):
            if dt.datetime.strptime(existing_date, "%Y-%m-%d") > \
                dt.datetime.strptime(new_date, "%Y-%m-%d"):
                continue

        # Update the `dyntable` columns
        dyntable['Latest Image'][i] = database.flat[filt]['filename'][-1]
        dyntable['UT Date of Last Flat'][i] = new_date
        dyntable['Count Rate (ADU/s)'][i] = \
                    (count_rate := np.mean(database.flat[filt]['crop_med']))
        dyntable['Exptime for 20k cts (s)'][i] = 20000. / count_rate

    # Save the dynamic portion back to a FITS bintable for future use
    dyntable.write(LMI_DYNTABLE, overwrite=True)

    # Merge the static and dynamic portions together
    lmi_filt = hstack([lmi_filt, dyntable])

    # Add formatting constraints to the `lmi_filt` table columns
    lmi_filt['Count Rate (ADU/s)'] = \
                Column(lmi_filt['Count Rate (ADU/s)'], format=two_sigfig)
    lmi_filt['Exptime for 20k cts (s)'] = \
                Column(lmi_filt['Exptime for 20k cts (s)'], format=two_sigfig)

    return lmi_filt


# Utility Functions ==========================================================#
def setup_confluence():
    """setup_confluence Set up the Confluence class instance

    Reads in the confluence.conf configuration file, which contains the URL,
    username, and password.  Also contained in the configuration file are
    the Confluence space and page title into which the updated table will be
    placed.

    Returns
    -------
    confluence : `atlassian.Confluence`
        Confluence class, initialized with credentials
    space : `str`
        The Confluence space containing the LMI Filter Information page
    title : `str`
        The page title for the LMI Filter Information
    """
    # Read in and parse the configuration file
    setup = lig_utils.confparsers.rawParser(
                                  ROZ_CONFIG.joinpath('confluence.conf'))
    setup = lig_workers.confUtils.assignConf(
                                  setup['confluenceSetup'],
                                  lig_utils.classes.baseTarget,
                                  backfill=True)
    # Return
    return Confluence( url=setup.host,
                       username=setup.user,
                       password=setup.password ), \
           setup.space, setup.lmi_filter_title


def read_lmi_static_table(table_type='ecsv'):
    """read_lmi_static_table Create the static portions of the LMI Filter Table

    This function reads in the information for the static portion of the
    LMI Filter Table, including the section headers (and locations).

    At present, there are two representations of these data: XML VOTABLE format
    and YAML-based ECSV (Astropy Table).

    Parameters
    ----------
    table_type : `str`, optional
        Type of table containing the data to be read in.  Choices are `ecsv`
        for the ECSV YAML-based AstroPy Table version, or `xml` for the
        XML-based VOTABLE protocol.  [Default: ecsv]

    Returns
    -------
    filter_table : `astropy.table.Table`
        The basic portions of the AstroPy table for LMI Filter Information
    section_head : `astropy.table.Table`
        The section headings for the HTML table

    Raises
    ------
    ValueError
        Raised if improper `table_type` passed to the function.
    """
    if table_type == 'xml':
        # Read in the XML table.
        votable = vo_parse(XML_TABLE)

        # The VOTable has both the LMI Filter Info and the section heads for the HTML
        filter_table = votable.get_table_by_index(0).to_table(use_names_over_ids=True)
        section_head = votable.get_table_by_index(1).to_table()

    elif table_type == 'ecsv':
        # Read in the ECSV tables (LMI Filter Info and the HTML section headings)
        filter_table = Table.read(ECSV_FILTERS)
        section_head = Table.read(ECSV_SECHEAD)

    else:
        raise ValueError(f"Table type {table_type} not recognized!")

    return filter_table, section_head


def construct_lmi_html_table(lmi_filt, section_head, filename, debug=False):
    """construct_lmi_html_table Construct the HTML table

    Use the AstroPy table to construct and beautify the HTML table for the
    LMI Filter Information page.  This function takes the output of the
    dynamically created table and does fixed operations to it to make it
    nicely human-readable.

    Parameters
    ----------
    lmi_filt : `astropy.table.Table`
        The LMI Filter Information table
    section_head : `astropy.table.Table`
        The section headings for the HTML table
    filename : `str`
        The filename for the HTML table
    debug : `bool`, optional
        Print debugging statements? [Default: False]
    """
    # Count the number of columns for use with the HTML table stuff below
    ncols = len(lmi_filt.colnames)

    # CSS stuff to make the HTML table pretty -- yeah, keep this hard-coded
    cssdict = {'css': 'table, td, th {\n      border: 1px solid black;\n   }\n'
                      '   table {\n      width: 100%;\n'
                      '      border-collapse: collapse;\n   }\n   '
                      'td {\n      padding: 10px;\n   }\n   th {\n'
                      '      color: white;\n      background: #6D6E70;\n   }'}

    # Use the AstroPy HTML functionality to get us most of the way there
    lmi_filt.write(filename, overwrite=True, htmldict=cssdict)

    # Now that AstroPy has done the hard work writing this table to HTML,
    #  we need to modify it a bit for visual clarity.  Use BeautifulSoup!
    with open(filename) as html:
        soup = BeautifulSoup(html, 'html.parser')

    # Add the `creation date` line to the body of the HTML above the table
    timestr = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    itdate = soup.new_tag('i')                # Italics, for the fun of it
    itdate.string = f"Table Auto-Generated {timestr} UTC by Roz."
    # Place the italicized date string ahead of the table
    soup.find('table').insert_before(itdate)
    if debug:
        print(f"HTML table timestamp: {timestr}")

    # Add the section headings for the different filter groups:
    for i,row in enumerate(soup.find_all('tr')):
        # At each row, search through the `section_head` table to correctly
        #  insert the appropriate section header
        for sechead in section_head:
            if i == sechead['insert_after']:
                row.insert_after(add_section_header(soup, ncols,
                                                    sechead['section'],
                                                    sechead['extra']))

    # Now that we've mucked with the HTML document, rewerite it to disk
    with open(filename, "wb") as f_output:
        f_output.write(soup.prettify("utf-8"))


def add_section_header(soup, ncols, text, extra=''):
    """add_section_header Put together the Section Headings for the HTML Table

    This is a bunch of BeautifulSoup tag stuff needed to make the section
    headings in the HTML table.  This function is purely a DRY block.

    Parameters
    ----------
    soup : `bs4.BeautifulSoup`
        The BeautifulSoup parsed-HTML object
    ncols : `int`
        Number of columns in the HTML table, needed for spanning
    text : `str`
        The bold/underlined text for the header
    extra : `str`, optional
        Regular text to appear after the bold/underlined text [Default: '']

    Returns
    -------
    `bs4.element.Tag`
        The newly tagged row for insertion into the HTML table
    """
    # Create the new row tag, and everything that goes inside it
    newrow = soup.new_tag('tr')
    # One column spanning the whole row, with Lowell-gray for background
    newcol = soup.new_tag('td', attrs={'colspan':ncols, 'bgcolor':'#DBDCDC'})
    # Bold/Underline the main `text` for the header; append to newcol
    bold = soup.new_tag('b')
    uline = soup.new_tag('u')
    uline.string = text
    bold.append(uline)
    newcol.append(bold)
    # Add any `extra` text in standard font after the bold/underline portion
    newcol.append('' if isinstance(extra, MaskedConstant) else extra)
    # Put the column tag inside the row tag
    newrow.append(newcol)
    # All done
    return newrow
