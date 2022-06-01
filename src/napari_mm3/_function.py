from __future__ import print_function, division
import copy
import datetime
import glob
import h5py
import json
import os
import multiprocessing
from multiprocessing import Pool
import matplotlib as mpl
font = {'family' : 'sans-serif',
        'weight' : 'normal',
        'size'   : 12}
mpl.rc('font', **font)
mpl.rcParams['pdf.fonttype'] = 42
from matplotlib.patches import Ellipse
import matplotlib.patches as mpatches
from matplotlib import image
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import napari
from napari_plugin_engine import napari_hook_implementation
import os
try:
    import cPickle as pickle
except:
    import pickle
from pprint import pprint
import pandas as pd
from pathlib import Path
import pims_nd2
import re
from scipy import ndimage as ndi
from scipy.signal import find_peaks_cwt
from scipy.optimize import curve_fit, leastsq
from skimage import io, segmentation, filters, morphology, measure
from skimage.transform import rotate
from skimage.feature import match_template, blob_log
from skimage.filters import threshold_otsu, median
from skimage.measure import regionprops, profile_line
from skimage.exposure import rescale_intensity
import seaborn as sns
sns.set(style='ticks', color_codes=True)
sns.set_palette('deep')
import six
import struct
import sys
import tensorflow as tf
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras import models, losses
from tensorflow.keras import backend as K
import time
import tifffile as tiff
import traceback
import warnings
import yaml

# This is the actual plugin function, where we export our function
# (The functions themselves are defined below)
@napari_hook_implementation
def napari_experimental_provide_function():
    # we can return a single function
    # or a tuple of (function, magicgui_options)
    # or a list of multiple functions with or without options, as shown here:
    #return [Segment, threshold, image_arithmetic]
    return [Compile, ChannelPicker, Segment, Track_Standard]

# 1.  First example, a simple function that thresholds an image and creates a labels layer
def threshold(data: "napari.types.ImageData", threshold: int) -> "napari.types.LabelsData":
    """Threshold an image and return a mask."""
    return (data > threshold).astype(int)

# print a warning
def warning(*objs):
    print(time.strftime("%H:%M:%S WARNING:", time.localtime()), *objs, file=sys.stderr)

# print information
def information(*objs):
    print(time.strftime("%H:%M:%S", time.localtime()), *objs, file=sys.stdout)

def julian_day_number():
    """
    Need this to solve a bug in pims_nd2.nd2reader.ND2_Reader instance initialization.
    The bug is in /usr/local/lib/python2.7/site-packages/pims_nd2/ND2SDK.py in function `jdn_to_datetime_local`, when the year number in the metadata (self._lim_metadata_desc) is not in the correct range. This causes a problem when calling self.metadata.
    https://en.wikipedia.org/wiki/Julian_day
    """
    dt=datetime.datetime.now()
    tt=dt.timetuple()
    jdn=(1461.*(tt.tm_year + 4800. + (tt.tm_mon - 14.)/12))/4. + (367.*(tt.tm_mon - 2. - 12.*((tt.tm_mon -14.)/12)))/12. - (3.*((tt.tm_year + 4900. + (tt.tm_mon - 14.)/12.)/100.))/4. + tt.tm_mday - 32075

    return jdn

def get_plane(filepath):
    pattern = r'(c\d+).tif'
    res = re.search(pattern,filepath)
    if (res != None):
        return res.group(1)
    else:
        return None

def get_fov(filepath):
    pattern = r'xy(\d+)\w*.tif'
    res = re.search(pattern,filepath)
    if (res != None):
        return int(res.group(1))
    else:
        return None

def get_time(filepath):
    pattern = r't(\d+)xy\w+.tif'
    res = re.search(pattern,filepath)
    if (res != None):
        return np.int_(res.group(1))
    else:
        return None

# loads and image stack from TIFF or HDF5 using mm3 conventions
def load_stack(fov_id, peak_id, color='c1', image_return_number=None):
    '''
    Loads an image stack.

    Supports reading TIFF stacks or HDF5 files.

    Parameters
    ----------
    fov_id : int
        The FOV id
    peak_id : int
        The peak (channel) id. Dummy None value incase color='empty'
    color : str
        The image stack type to return. Can be:
        c1 : phase stack
        cN : where n is an integer for arbitrary color channel
        sub : subtracted images
        seg : segmented images
        empty : get the empty channel for this fov, slightly different

    Returns
    -------
    image_stack : np.ndarray
        The image stack through time. Shape is (t, y, x)
    '''

    # things are slightly different for empty channels
    if 'empty' in color:
        if params['output'] == 'TIFF':
            img_filename = params['experiment_name'] + '_xy%03d_%s.tif' % (fov_id, color)

            with tiff.TiffFile(os.path.join(params['empty_dir'],img_filename)) as tif:
                img_stack = tif.asarray()

        if params['output'] == 'HDF5':
            with h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'r') as h5f:
                img_stack = h5f[color][:]

        return img_stack

    # load normal images for either TIFF or HDF5
    if params['output'] == 'TIFF':
        if color[0] == 'c':
            img_dir = params['chnl_dir']
            img_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, color)
        elif 'sub' in color:
            img_dir = params['sub_dir']
            img_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, color)
        elif 'foci' in color:
            img_dir = params['foci_seg_dir']
            img_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, color)
        elif 'seg' in color:
            last='seg_otsu'
            if 'seg_img' in params.keys():
                last=params['seg_img']
            if 'track' in params.keys():
                last=params['track']['seg_img']

            img_dir = params['seg_dir']
            img_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, last)
        else:
            img_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, color)

        with tiff.TiffFile(os.path.join(img_dir, img_filename)) as tif:
            img_stack = tif.asarray()

    if params['output'] == 'HDF5':
        with h5py.File(os.path.join(params['hdf5_dir'], 'xy%03d.hdf5' % fov_id), 'r') as h5f:
            # normal naming
            # need to use [:] to get a copy, else it references the closed hdf5 dataset
            img_stack = h5f['channel_%04d/p%04d_%s' % (peak_id, peak_id, color)][:]

    return img_stack

# load the time table and add it to the global params
def load_time_table():
    '''Add the time table dictionary to the params global dictionary.
    This is so it can be used during Cell creation.
    '''

    # try first for yaml, then for pkl
    try:
        with open(os.path.join(params['ana_dir'], 'time_table.yaml'), 'rb') as time_table_file:
            params['time_table'] = yaml.safe_load(time_table_file)
    except:
        with open(os.path.join(params['ana_dir'], 'time_table.pkl'), 'rb') as time_table_file:
            params['time_table'] = pickle.load(time_table_file)

    return

# function for loading the channel masks
def load_channel_masks():
    '''Load channel masks dictionary. Should be .yaml but try pickle too.
    '''
    information("Loading channel masks dictionary.")

    # try loading from .yaml before .pkl
    try:
        information('Path:', os.path.join(params['ana_dir'], 'channel_masks.yaml'))
        with open(os.path.join(params['ana_dir'], 'channel_masks.yaml'), 'r') as cmask_file:
            channel_masks = yaml.safe_load(cmask_file)
    except:
        warning('Could not load channel masks dictionary from .yaml.')

        try:
            information('Path:', os.path.join(params['ana_dir'], 'channel_masks.pkl'))
            with open(os.path.join(params['ana_dir'], 'channel_masks.pkl'), 'rb') as cmask_file:
                channel_masks = pickle.load(cmask_file)
        except ValueError:
            warning('Could not load channel masks dictionary from .pkl.')

    return channel_masks

# function for loading the specs file
def load_specs():
    '''Load specs file which indicates which channels should be analyzed, used as empties, or ignored.'''

    try:
        with open(os.path.join(params['ana_dir'], 'specs.yaml'), 'r') as specs_file:
            specs = yaml.safe_load(specs_file)
    except:
        try:
            with open(os.path.join(params['ana_dir'], 'specs.pkl'), 'rb') as specs_file:
                specs = pickle.load(specs_file)
        except ValueError:
            warning('Could not load specs file.')

    return specs

### functions for dealing with raw TIFF images

# get params is the major function which processes raw TIFF images
def get_initial_tif_params(image_filename):
    '''This is a function for getting the information
    out of an image for later trap identification, cropping, and aligning with Unet. It loads a tiff file and pulls out the image metadata.

    it returns a dictionary like this for each image:

    'filename': image_filename,
    'fov' : image_metadata['fov'], # fov id
    't' : image_metadata['t'], # time point
    'jdn' : image_metadata['jdn'], # absolute julian time
    'x' : image_metadata['x'], # x position on stage [um]
    'y' : image_metadata['y'], # y position on stage [um]
    'plane_names' : image_metadata['plane_names'] # list of plane names

    Called by
    mm3_Compile.py __main__

    Calls
    mm3.extract_metadata
    mm3.find_channels
    '''

    try:
        # open up file and get metadata
        with tiff.TiffFile(os.path.join(params['TIFF_dir'],image_filename)) as tif:
            image_data = tif.asarray()
            #print(image_data.shape) # uncomment for debug
            #if len(image_data.shape) == 2:
            #    img_shape = [image_data.shape[0],image_data.shape[1]]
            #else:
            img_shape = [image_data.shape[1],image_data.shape[2]]
            plane_list = [str(i+1) for i in range(image_data.shape[0])]
            #print(plane_list) # uncomment for debug

            if params['TIFF_source'] == 'elements':
                image_metadata = get_tif_metadata_elements(tif)
            elif params['TIFF_source'] == 'nd2ToTIFF':
                image_metadata = get_tif_metadata_nd2ToTIFF(tif)
            else:
                image_metadata = get_tif_metadata_filename(tif)

        information('Analyzed %s' % image_filename)

        # return the file name, the data for the channels in that image, and the metadata
        return {'filepath': os.path.join(params['TIFF_dir'], image_filename),
                'fov' : image_metadata['fov'], # fov id
                't' : image_metadata['t'], # time point
                'jd' : image_metadata['jd'], # absolute julian time
                'x' : image_metadata['x'], # x position on stage [um]
                'y' : image_metadata['y'], # y position on stage [um]
                'planes' : plane_list, # list of plane names
                'shape' : img_shape} # image shape x y in pixels

    except:
        warning('Failed get_params for ' + image_filename.split("/")[-1])
        print(sys.exc_info()[0])
        print(sys.exc_info()[1])
        print(traceback.print_tb(sys.exc_info()[2]))
        return {'filepath': os.path.join(params['TIFF_dir'],image_filename), 'analyze_success': False}

# get params is the major function which processes raw TIFF images
def get_tif_params(image_filename, find_channels=True):
    '''This is a damn important function for getting the information
    out of an image. It loads a tiff file, pulls out the image data, and the metadata,
    including the location of the channels if flagged.

    it returns a dictionary like this for each image:

    'filename': image_filename,
    'fov' : image_metadata['fov'], # fov id
    't' : image_metadata['t'], # time point
    'jdn' : image_metadata['jdn'], # absolute julian time
    'x' : image_metadata['x'], # x position on stage [um]
    'y' : image_metadata['y'], # y position on stage [um]
    'plane_names' : image_metadata['plane_names'] # list of plane names
    'channels': cp_dict, # dictionary of channel locations, in the case of Unet-based channel segmentation, it's a dictionary of channel labels

    Called by
    mm3_Compile.py __main__

    Calls
    mm3.extract_metadata
    mm3.find_channels
    '''

    try:
        # open up file and get metadata
        with tiff.TiffFile(os.path.join(params['TIFF_dir'],image_filename)) as tif:
            image_data = tif.asarray()

            if params['TIFF_source'] == 'elements':
                image_metadata = get_tif_metadata_elements(tif)
            elif params['TIFF_source'] == 'nd2ToTIFF':
                image_metadata = get_tif_metadata_nd2ToTIFF(tif)
            else:
                image_metadata = get_tif_metadata_filename(tif)

        # look for channels if flagged
        if find_channels:
            # fix the image orientation and get the number of planes
            image_data = fix_orientation(image_data)

            # if the image data has more than 1 plane restrict image_data to phase,
            # which should have highest mean pixel data
            if len(image_data.shape) > 2:
                #ph_index = np.argmax([np.mean(image_data[ci]) for ci in range(image_data.shape[0])])
                ph_index = int(params['phase_plane'][1:]) - 1
                image_data = image_data[ph_index]

            # get shape of single plane
            img_shape = [image_data.shape[0], image_data.shape[1]]

            # find channels on the processed image
            chnl_loc_dict = find_channel_locs(image_data)

        information('Analyzed %s' % image_filename)

        # return the file name, the data for the channels in that image, and the metadata
        return {'filepath': os.path.join(params['TIFF_dir'], image_filename),
                'fov' : image_metadata['fov'], # fov id
                't' : image_metadata['t'], # time point
                'jd' : image_metadata['jd'], # absolute julian time
                'x' : image_metadata['x'], # x position on stage [um]
                'y' : image_metadata['y'], # y position on stage [um]
                'planes' : image_metadata['planes'], # list of plane names
                'shape' : img_shape, # image shape x y in pixels
                # 'channels' : {1 : {'A' : 1, 'B' : 2}, 2 : {'C' : 3, 'D' : 4}}}
                'channels' : chnl_loc_dict} # dictionary of channel locations

    except:
        warning('Failed get_params for ' + image_filename.split("/")[-1])
        print(sys.exc_info()[0])
        print(sys.exc_info()[1])
        print(traceback.print_tb(sys.exc_info()[2]))
        return {'filepath': os.path.join(params['TIFF_dir'],image_filename), 'analyze_success': False}

# finds metdata in a tiff image which has been expoted with Nikon Elements.
def get_tif_metadata_elements(tif):
    '''This function pulls out the metadata from a tif file and returns it as a dictionary.
    This if tiff files as exported by Nikon Elements as a stacked tiff, each for one tpoint.
    tif is an opened tif file (using the package tifffile)


    arguments:
        fname (tifffile.TiffFile): TIFF file object from which data will be extracted
    returns:
        dictionary of values:
            'jdn' (float)
            'x' (float)
            'y' (float)
            'plane_names' (list of strings)

    Called by
    mm3.Compile

    '''

    # image Metadata
    idata = { 'fov': -1,
              't' : -1,
              'jd': -1 * 0.0,
              'x': -1 * 0.0,
              'y': -1 * 0.0,
              'planes': []}

    # get the fov and t simply from the file name
    idata['fov'] = int(tif.fname.split('xy')[1].split('.tif')[0])
    idata['t'] = int(tif.fname.split('xy')[0].split('t')[-1])

    # a page is plane, or stack, in the tiff. The other metdata is hidden down in there.
    for page in tif:
        for tag in page.tags.values():
            #print("Checking tag",tag.name,tag.value)
            t = tag.name, tag.value
            t_string = u""
            time_string = u""
            # Interesting tag names: 65330, 65331 (binary data; good stuff), 65332
            # we wnat to work with the tag of the name 65331
            # if the tag name is not in the set of tegs we find interesting then skip this cycle of the loop
            if tag.name not in ('65331', '65332', 'strip_byte_counts', 'image_width', 'orientation', 'compression', 'new_subfile_type', 'fill_order', 'max_sample_value', 'bits_per_sample', '65328', '65333'):
                #print("*** " + tag.name)
                #print(tag.value)
                pass
            #if tag.name == '65330':
            #    return tag.value
            if tag.name in ('65331'):
                # make info list a list of the tag values 0 to 65535 by zipoing up a paired list of two bytes, at two byte intervals i.e. ::2
                # note that 0X100 is hex for 256
                infolist = [a+b*0x100 for a,b in zip(tag.value[0::2], tag.value[1::2])]
                # get char values for each element in infolist
                for c_entry in range(0, len(infolist)):
                    # the element corresponds to an ascii char for a letter or bracket (and a few other things)
                    if infolist[c_entry] < 127 and infolist[c_entry] > 64:
                        # add the letter to the unicode string t_string
                        t_string += chr(infolist[c_entry])
                    #elif infolist[c_entry] == 0:
                    #    continue
                    else:
                        t_string += " "

                # this block will find the dTimeAbsolute and print the subsequent integers
                # index 170 is counting seconds, and rollover of index 170 leads to increment of index 171
                # rollover of index 171 leads to increment of index 172
                # get the position of the array by finding the index of the t_string at which dTimeAbsolute is listed not that 2*len(dTimeAbsolute)=26
                #print(t_string)

                arraypos = t_string.index("dXPos") * 2 + 16
                xarr = tag.value[arraypos:arraypos+4]
                b = ''.join(chr(i) for i in xarr)
                idata['x'] = float(struct.unpack('<f', b)[0])

                arraypos = t_string.index("dYPos") * 2 + 16
                yarr = tag.value[arraypos:arraypos+4]
                b = ''.join(chr(i) for i in yarr)
                idata['y'] = float(struct.unpack('<f', b)[0])

                arraypos = t_string.index("dTimeAbsolute") * 2 + 26
                shortarray = tag.value[arraypos+2:arraypos+10]
                b = ''.join(chr(i) for i in shortarray)
                idata['jd'] = float(struct.unpack('<d', b)[0])

                # extract plane names
                il = [a+b*0x100 for a,b in zip(tag.value[0::2], tag.value[1::2])]
                li = [a+b*0x100 for a,b in zip(tag.value[1::2], tag.value[2::2])]

                strings = list(zip(il, li))

                allchars = ""
                for c_entry in range(0, len(strings)):
                    if 31 < strings[c_entry][0] < 127:
                        allchars += chr(strings[c_entry][0])
                    elif 31 < strings[c_entry][1] < 127:
                        allchars += chr(strings[c_entry][1])
                    else:
                        allchars += " "

                allchars = re.sub(' +',' ', allchars)

                words = allchars.split(" ")

                planes = []
                for idx in [i for i, x in enumerate(words) if x == "sOpticalConfigName"]:
                    planes.append(words[idx+1])

                idata['planes'] = planes

    return idata

# finds metdata in a tiff image which has been expoted with nd2ToTIFF.py.
def get_tif_metadata_nd2ToTIFF(tif):
    '''This function pulls out the metadata from a tif file and returns it as a dictionary.
    This if tiff files as exported by the mm3 function mm3_nd2ToTIFF.py. All the metdata
    is found in that script and saved in json format to the tiff, so it is simply extracted here

    Paramters:
        tif: TIFF file object from which data will be extracted
    Returns:
        dictionary of values:
            'fov': int,
            't' : int,
            'jdn' (float)
            'x' (float)
            'y' (float)
            'planes' (list of strings)

    Called by
    mm3_Compile.get_tif_params

    '''
    # get the first page of the tiff and pull out image description
    # this dictionary should be in the above form

    for tag in tif.pages[0].tags:
        if tag.name=="ImageDescription":
            idata=tag.value
            break

    #print(idata)
    idata = json.loads(idata)
    return idata

# Finds metadata from the filename
def get_tif_metadata_filename(tif):
    '''This function pulls out the metadata from a tif file and returns it as a dictionary.
    This just gets the tiff metadata from the filename and is a backup option when the known format of the metadata is not known.

    Paramters:
        tif: TIFF file object from which data will be extracted
    Returns:
        dictionary of values:
            'fov': int,
            't' : int,
            'jdn' (float)
            'x' (float)
            'y' (float)

    Called by
    mm3_Compile.get_tif_params

    '''
    idata = {'fov' : get_fov(tif.filename), # fov id
             't' : get_time(tif.filename), # time point
             'jd' : -1 * 0.0, # absolute julian time
             'x' : -1 * 0.0, # x position on stage [um]
             'y' : -1 * 0.0} # y position on stage [um]

    return idata

# make a lookup time table for converting nominal time to elapsed time in seconds
def make_time_table(analyzed_imgs):
    '''
    Loops through the analyzed images and uses the jd time in the metadata to find the elapsed
    time in seconds that each picture was taken. This is later used for more accurate elongation
    rate calculation.

    Parametrs
    ---------
    analyzed_imgs : dict
        The output of get_tif_params.
    params['use_jd'] : boolean
        If set to True, 'jd' time will be used from the image metadata to use to create time table. Otherwise the 't' index will be used, and the parameter 'seconds_per_time_index' will be used from the parameters.yaml file to convert to seconds.

    Returns
    -------
    time_table : dict
        Look up dictionary with keys for the FOV and then the time point.
    '''
    information('Making time table...')

    # initialize
    time_table = {}

    first_time = float('inf')

    # need to go through the data once to find the first time
    for iname, idata in six.iteritems(analyzed_imgs):
        if params['use_jd']:
            if idata['jd'] < first_time:
                first_time = idata['jd']
        else:
            if idata['t'] < first_time:
                first_time = idata['t']

        # init dictionary for specific times per FOV
        if idata['fov'] not in time_table:
            time_table[idata['fov']] = {}

    for iname, idata in six.iteritems(analyzed_imgs):
        if params['use_jd']:
            # convert jd time to elapsed time in seconds
            t_in_seconds = np.around((idata['jd'] - first_time) * 24*60*60, decimals=0).astype('uint32')
        else:
            t_in_seconds = np.around((idata['t'] - first_time) * params['moviemaker']['seconds_per_time_index'], decimals=0).astype('uint32')

        time_table[int(idata['fov'])][int(idata['t'])] = int(t_in_seconds)

    # save to .pkl. This pkl will be loaded into the params
    # with open(os.path.join(params['ana_dir'], 'time_table.pkl'), 'wb') as time_table_file:
    #     pickle.dump(time_table, time_table_file, protocol=pickle.HIGHEST_PROTOCOL)
    # with open(os.path.join(params['ana_dir'], 'time_table.txt'), 'w') as time_table_file:
    #     pprint(time_table, stream=time_table_file)
    with open(os.path.join(params['ana_dir'], 'time_table.yaml'), 'w') as time_table_file:
        yaml.dump(data=time_table, stream=time_table_file, default_flow_style=False, tags=None)
    information('Time table saved.')

    return time_table

# saves traps sliced via Unet
def save_tiffs(imgDict, analyzed_imgs, fov_id):

    savePath = os.path.join(params['experiment_directory'],
                            params['analysis_directory'],
                            params['chnl_dir'])
    img_names = [key for key in analyzed_imgs.keys()]
    image_params = analyzed_imgs[img_names[0]]

    for peak,img in six.iteritems(imgDict):

        img = img.astype('uint16')
        if not os.path.isdir(savePath):
            os.mkdir(savePath)

        for planeNumber in image_params['planes']:

            channel_filename = os.path.join(savePath, params['experiment_name'] + '_xy{0:0=3}_p{1:0=4}_c{2}.tif'.format(fov_id, peak, planeNumber))
            io.imsave(channel_filename, img[:,:,:,int(planeNumber)-1])

# slice_and_write cuts up the image files one at a time and writes them out to tiff stacks
def tiff_stack_slice_and_write(images_to_write, channel_masks, analyzed_imgs):
    '''Writes out 4D stacks of TIFF images per channel.
    Loads all tiffs from and FOV into memory and then slices all time points at once.

    Called by
    __main__
    '''

    # make an array of images and then concatenate them into one big stack
    image_fov_stack = []

    # go through list of images and get the file path
    for n, image in enumerate(images_to_write):
        # analyzed_imgs dictionary will be found in main scope. [0] is the key, [1] is jd
        image_params = analyzed_imgs[image[0]]

        information("Loading %s." % image_params['filepath'].split('/')[-1])

        if n == 1:
            # declare identification variables for saving using first image
            fov_id = image_params['fov']

        # load the tif and store it in array
        with tiff.TiffFile(image_params['filepath']) as tif:
            image_data = tif.asarray()

        # channel finding was also done on images after orientation was fixed
        image_data = fix_orientation(image_data)

        # add additional axis if the image is flat
        if len(image_data.shape) == 2:
            image_data = np.expand_dims(image_data, 0)

        # change axis so it goes Y, X, Plane
        image_data = np.rollaxis(image_data, 0, 3)

        # add it to list. The images should be in time order
        image_fov_stack.append(image_data)

    # concatenate the list into one big ass stack
    image_fov_stack = np.stack(image_fov_stack, axis=0)

    # cut out the channels as per channel masks for this fov
    for peak, channel_loc in six.iteritems(channel_masks[fov_id]):
        #information('Slicing and saving channel peak %s.' % channel_filename.split('/')[-1])
        information('Slicing and saving channel peak %d.' % peak)

        # channel masks should only contain ints, but you can use this for hard fix
        # for i in range(len(channel_loc)):
        #     for j in range(len(channel_loc[i])):
        #         channel_loc[i][j] = int(channel_loc[i][j])

        # slice out channel.
        # The function should recognize the shape length as 4 and cut all time points
        channel_stack = cut_slice(image_fov_stack, channel_loc)

        # save a different time stack for all colors
        for color_index in range(channel_stack.shape[3]):
            # this is the filename for the channel
            # # chnl_dir and p will be looked for in the scope above (__main__)
            channel_filename = os.path.join(params['chnl_dir'], params['experiment_name'] + '_xy%03d_p%04d_c%1d.tif' % (fov_id, peak, color_index+1))
            # save stack
            tiff.imsave(channel_filename, channel_stack[:,:,:,color_index], compress=4)

    return

# saves traps sliced via Unet to an hdf5 file
def save_hdf5(imgDict, img_names, analyzed_imgs, fov_id, channel_masks):
    '''Writes out 4D stacks of images to an HDF5 file.

    Called by
    mm3_Compile.py
    '''

    savePath = params['hdf5_dir']

    if not os.path.isdir(savePath):
        os.mkdir(savePath)

    img_times = [analyzed_imgs[key]['t'] for key in img_names]
    img_jds = [analyzed_imgs[key]['jd'] for key in img_names]
    fov_ids = [analyzed_imgs[key]['fov'] for key in img_names]

    # get image_params from first image from current fov
    image_params = analyzed_imgs[img_names[0]]

    # establish some variables for hdf5 attributes
    fov_id = image_params['fov']
    x_loc = image_params['x']
    y_loc = image_params['y']
    image_shape = image_params['shape']
    image_planes = image_params['planes']

    fov_channel_masks = channel_masks[fov_id]

    with h5py.File(os.path.join(savePath,'{}_xy{:0=2}.hdf5'.format(params['experiment_name'],fov_id)), 'w', libver='earliest') as h5f:

        # add in metadata for this FOV
        # these attributes should be common for all channel
        h5f.attrs.create('fov_id', fov_id)
        h5f.attrs.create('stage_x_loc', x_loc)
        h5f.attrs.create('stage_y_loc', y_loc)
        h5f.attrs.create('image_shape', image_shape)
        # encoding is because HDF5 has problems with numpy unicode
        h5f.attrs.create('planes', [plane.encode('utf8') for plane in image_planes])
        h5f.attrs.create('peaks', sorted([key for key in imgDict.keys()]))

        # this is for things that change across time, for these create a dataset
        img_names = np.asarray(img_names)
        img_names = np.expand_dims(img_names, 1)
        img_names = img_names.astype('S100')
        h5ds = h5f.create_dataset(u'filenames', data=img_names,
                                  chunks=True, maxshape=(None, 1), dtype='S100',
                                  compression="gzip", shuffle=True, fletcher32=True)
        h5ds = h5f.create_dataset(u'times', data=np.expand_dims(img_times, 1),
                                  chunks=True, maxshape=(None, 1),
                                  compression="gzip", shuffle=True, fletcher32=True)
        h5ds = h5f.create_dataset(u'times_jd', data=np.expand_dims(img_jds, 1),
                                  chunks=True, maxshape=(None, 1),
                                  compression="gzip", shuffle=True, fletcher32=True)

        # cut out the channels as per channel masks for this fov
        for peak,channel_stack in six.iteritems(imgDict):

            channel_stack = channel_stack.astype('uint16')
            # create group for this trap
            h5g = h5f.create_group('channel_%04d' % peak)

            # add attribute for peak_id, channel location
            # add attribute for peak_id, channel location
            h5g.attrs.create('peak_id', peak)
            channel_loc = fov_channel_masks[peak]
            h5g.attrs.create('channel_loc', channel_loc)

            # save a different dataset for all colors
            for color_index in range(channel_stack.shape[3]):

                # create the dataset for the image. Review docs for these options.
                h5ds = h5g.create_dataset(u'p%04d_c%1d' % (peak, color_index+1),
                                data=channel_stack[:,:,:,color_index],
                                chunks=(1, channel_stack.shape[1], channel_stack.shape[2]),
                                maxshape=(None, channel_stack.shape[1], channel_stack.shape[2]),
                                compression="gzip", shuffle=True, fletcher32=True)

                # h5ds.attrs.create('plane', image_planes[color_index].encode('utf8'))

                # write the data even though we have more to write (free up memory)
                h5f.flush()

    return

# same thing as tiff_stack_slice_and_write but do it for hdf5
def hdf5_stack_slice_and_write(images_to_write, channel_masks, analyzed_imgs):
    '''Writes out 4D stacks of TIFF images to an HDF5 file.

    Called by
    __main__
    '''

    # make an array of images and then concatenate them into one big stack
    image_fov_stack = []

    # make arrays for filenames and times
    image_filenames = []
    image_times = [] # times is still an integer but may be indexed arbitrarily
    image_jds = [] # jds = julian dates (times)

    # go through list of images, load and fix them, and create arrays of metadata
    for n, image in enumerate(images_to_write):
        image_name = image[0] # [0] is the key, [1] is jd

        # analyzed_imgs dictionary will be found in main scope.
        image_params = analyzed_imgs[image_name]
        information("Loading %s." % image_params['filepath'].split('/')[-1])

        # add information to metadata arrays
        image_filenames.append(image_name)
        image_times.append(image_params['t'])
        image_jds.append(image_params['jd'])

        # declare identification variables for saving using first image
        if n == 1:
            # same across fov
            fov_id = image_params['fov']
            x_loc = image_params['x']
            y_loc = image_params['y']
            image_shape = image_params['shape']
            image_planes = image_params['planes']

        # load the tif and store it in array
        with tiff.TiffFile(image_params['filepath']) as tif:
            image_data = tif.asarray()

        # channel finding was also done on images after orientation was fixed
        image_data = fix_orientation(image_data)

        # add additional axis if the image is flat
        if len(image_data.shape) == 2:
            image_data = np.expand_dims(image_data, 0)

        #change axis so it goes X, Y, Plane
        image_data = np.rollaxis(image_data, 0, 3)

        # add it to list. The images should be in time order
        image_fov_stack.append(image_data)

    # concatenate the list into one big ass stack
    image_fov_stack = np.stack(image_fov_stack, axis=0)

    # create the HDF5 file for the FOV, first time this is being done.
    with h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'w', libver='earliest') as h5f:

        # add in metadata for this FOV
        # these attributes should be common for all channel
        h5f.attrs.create('fov_id', fov_id)
        h5f.attrs.create('stage_x_loc', x_loc)
        h5f.attrs.create('stage_y_loc', y_loc)
        h5f.attrs.create('image_shape', image_shape)
        # encoding is because HDF5 has problems with numpy unicode
        h5f.attrs.create('planes', [plane.encode('utf8') for plane in image_planes])
        h5f.attrs.create('peaks', sorted(channel_masks[fov_id].keys()))

        # this is for things that change across time, for these create a dataset
        h5ds = h5f.create_dataset(u'filenames', data=np.expand_dims(image_filenames, 1),
                                  chunks=True, maxshape=(None, 1), dtype='S100',
                                  compression="gzip", shuffle=True, fletcher32=True)
        h5ds = h5f.create_dataset(u'times', data=np.expand_dims(image_times, 1),
                                  chunks=True, maxshape=(None, 1),
                                  compression="gzip", shuffle=True, fletcher32=True)
        h5ds = h5f.create_dataset(u'times_jd', data=np.expand_dims(image_jds, 1),
                                  chunks=True, maxshape=(None, 1),
                                  compression="gzip", shuffle=True, fletcher32=True)

        # cut out the channels as per channel masks for this fov
        for peak, channel_loc in six.iteritems(channel_masks[fov_id]):
            #information('Slicing and saving channel peak %s.' % channel_filename.split('/')[-1])
            information('Slicing and saving channel peak %d.' % peak)

            # create group for this channel
            h5g = h5f.create_group('channel_%04d' % peak)

            # add attribute for peak_id, channel location
            h5g.attrs.create('peak_id', peak)
            h5g.attrs.create('channel_loc', channel_loc)

            # channel masks should only contain ints, but you can use this for a hard fix
            # for i in range(len(channel_loc)):
            #     for j in range(len(channel_loc[i])):
            #         channel_loc[i][j] = int(channel_loc[i][j])

            # slice out channel.
            # The function should recognize the shape length as 4 and cut all time points
            channel_stack = cut_slice(image_fov_stack, channel_loc)

            # save a different dataset for all colors
            for color_index in range(channel_stack.shape[3]):

                # create the dataset for the image. Review docs for these options.
                h5ds = h5g.create_dataset(u'p%04d_c%1d' % (peak, color_index+1),
                                data=channel_stack[:,:,:,color_index],
                                chunks=(1, channel_stack.shape[1], channel_stack.shape[2]),
                                maxshape=(None, channel_stack.shape[1], channel_stack.shape[2]),
                                compression="gzip", shuffle=True, fletcher32=True)

                # h5ds.attrs.create('plane', image_planes[color_index].encode('utf8'))

                # write the data even though we have more to write (free up memory)
                h5f.flush()

    return

# finds the location of channels in a tif
def find_channel_locs(image_data):
    '''Finds the location of channels from a phase contrast image. The channels are returned in
    a dictionary where the key is the x position of the channel in pixel and the value is a
    dicionary with the open and closed end in pixels in y.


    Called by
    mm3_Compile.get_tif_params

    '''

    # declare temp variables from yaml parameter dict.
    chan_w = params['compile']['channel_width']
    chan_sep = params['compile']['channel_separation']
    crop_wp = int(params['compile']['channel_width_pad'] + chan_w/2)
    chan_snr = params['compile']['channel_detection_snr']

    # Detect peaks in the x projection (i.e. find the channels)
    projection_x = image_data.sum(axis=0).astype(np.int32)
    # find_peaks_cwt is a function which attempts to find the peaks in a 1-D array by
    # convolving it with a wave. here the wave is the default Mexican hat wave
    # but the minimum signal to noise ratio is specified
    # *** The range here should be a parameter or changed to a fraction.
    peaks = find_peaks_cwt(projection_x, np.arange(chan_w-5,chan_w+5), min_snr=chan_snr)

    # If the left-most peak position is within half of a channel separation,
    # discard the channel from the list.
    if peaks[0] < (chan_sep / 2):
        peaks = peaks[1:]
    # If the diference between the right-most peak position and the right edge
    # of the image is less than half of a channel separation, discard the channel.
    if image_data.shape[1] - peaks[-1] < (chan_sep / 2):
        peaks = peaks[:-1]

    # Find the average channel ends for the y-projected image
    projection_y = image_data.sum(axis=1)
    # find derivative, must use int32 because it was unsigned 16b before.
    proj_y_d = np.diff(projection_y.astype(np.int32))
    # use the top third to look for closed end, is pixel location of highest deriv
    onethirdpoint_y = int(projection_y.shape[0]/3.0)
    default_closed_end_px = proj_y_d[:onethirdpoint_y].argmax()
    # use bottom third to look for open end, pixel location of lowest deriv
    twothirdpoint_y = int(projection_y.shape[0]*2.0/3.0)
    default_open_end_px = twothirdpoint_y + proj_y_d[twothirdpoint_y:].argmin()
    default_length = default_open_end_px - default_closed_end_px # used for checks

    # go through peaks and assign information
    # dict for channel dimensions
    chnl_loc_dict = {}
    # key is peak location, value is dict with {'closed_end_px': px, 'open_end_px': px}

    for peak in peaks:
        # set defaults
        chnl_loc_dict[peak] = {'closed_end_px': default_closed_end_px,
                                 'open_end_px': default_open_end_px}
        # redo the previous y projection finding with just this channel
        channel_slice = image_data[:, peak-crop_wp:peak+crop_wp]
        slice_projection_y = channel_slice.sum(axis = 1)
        slice_proj_y_d = np.diff(slice_projection_y.astype(np.int32))
        slice_closed_end_px = slice_proj_y_d[:onethirdpoint_y].argmax()
        slice_open_end_px = twothirdpoint_y + slice_proj_y_d[twothirdpoint_y:].argmin()
        slice_length = slice_open_end_px - slice_closed_end_px

        # check if these values make sense. If so, use them. If not, use default
        # make sure lenght is not 30 pixels bigger or smaller than default
        # *** This 15 should probably be a parameter or at least changed to a fraction.
        if slice_length + 15 < default_length or slice_length - 15 > default_length:
            continue
        # make sure ends are greater than 15 pixels from image edge
        if slice_closed_end_px < 15 or slice_open_end_px > image_data.shape[0] - 15:
            continue

        # if you made it to this point then update the entry
        chnl_loc_dict[peak] = {'closed_end_px' : slice_closed_end_px,
                                 'open_end_px' : slice_open_end_px}

    return chnl_loc_dict

# make masks from initial set of images (same images as clusters)
def make_masks(analyzed_imgs):
    '''
    Make masks goes through the channel locations in the image metadata and builds a consensus
    Mask for each image per fov, which it returns as dictionary named channel_masks.
    The keys in this dictionary are fov id, and the values is a another dictionary. This dict's keys are channel locations (peaks) and the values is a [2][2] array:
    [[minrow, maxrow],[mincol, maxcol]] of pixel locations designating the corner of each mask
    for each channel on the whole image

    One important consequence of these function is that the channel ids and the size of the
    channel slices are decided now. Updates to mask must coordinate with these values.

    Parameters
    analyzed_imgs : dict
        image information created by get_params

    Returns
    channel_masks : dict
        dictionary of consensus channel masks.

    Called By
    mm3_Compile.py

    Calls
    '''
    information("Determining initial channel masks...")

    # declare temp variables from yaml parameter dict.
    crop_wp = int(params['compile']['channel_width_pad'] + params['compile']['channel_width']/2)
    chan_lp = int(params['compile']['channel_length_pad'])

    #intiaize dictionary
    channel_masks = {}

    # get the size of the images (hope they are the same)
    for img_k in analyzed_imgs.keys():
        img_v = analyzed_imgs[img_k]
        image_rows = img_v['shape'][0] # x pixels
        image_cols = img_v['shape'][1] # y pixels
        break # just need one. using iteritems mean the whole dict doesn't load

    # get the fov ids
    fovs = []
    for img_k in analyzed_imgs.keys():
        img_v = analyzed_imgs[img_k]
        if img_v['fov'] not in fovs:
            fovs.append(img_v['fov'])

    # max width and length across all fovs. channels will get expanded by these values
    # this important for later updates to the masks, which should be the same
    max_chnl_mask_len = 0
    max_chnl_mask_wid = 0

    # for each fov make a channel_mask dictionary from consensus mask
    for fov in fovs:
        # initialize a the dict and consensus mask
        channel_masks_1fov = {} # dict which holds channel masks {peak : [[y1, y2],[x1,x2]],...}
        consensus_mask = np.zeros([image_rows, image_cols]) # mask for labeling

        # bring up information for each image
        for img_k in analyzed_imgs.keys():
            img_v = analyzed_imgs[img_k]
            # skip this one if it is not of the current fov
            if img_v['fov'] != fov:
                continue

            # for each channel in each image make a single mask
            img_chnl_mask = np.zeros([image_rows, image_cols])

            # and add the channel mask to it
            for chnl_peak, peak_ends in six.iteritems(img_v['channels']):
                # pull out the peak location and top and bottom location
                # and expand by padding (more padding done later for width)
                x1 = max(chnl_peak - crop_wp, 0)
                x2 = min(chnl_peak + crop_wp, image_cols)
                y1 = max(peak_ends['closed_end_px'] - chan_lp, 0)
                y2 = min(peak_ends['open_end_px'] + chan_lp, image_rows)

                # add it to the mask for this image
                img_chnl_mask[y1:y2, x1:x2] = 1

            # add it to the consensus mask
            consensus_mask += img_chnl_mask

        # Normalize concensus mask between 0 and 1.
        consensus_mask = consensus_mask.astype('float32') / float(np.amax(consensus_mask))

        # threshhold and homogenize each channel mask within the mask, label them
        # label when value is above 0.1 (so 90% occupancy), transpose.
        # the [0] is for the array ([1] is the number of regions)
        # It transposes and then transposes again so regions are labeled left to right
        # clear border it to make sure the channels are off the edge
        consensus_mask = ndi.label(consensus_mask)[0]

        # go through each label
        for label in np.unique(consensus_mask):
            if label == 0: # label zero is the background
                continue
            binary_core = consensus_mask == label

            # clean up the rough edges
            poscols = np.any(binary_core, axis = 0) # column positions where true (any)
            posrows = np.any(binary_core, axis = 1) # row positions where true (any)

            # channel_id givin by horizontal position
            # this is important. later updates to the positions will have to check
            # if their channels contain this median value to match up
            channel_id = int(np.median(np.where(poscols)[0]))

            # store the edge locations of the channel mask in the dictionary. Will be ints
            min_row = np.min(np.where(posrows)[0])
            max_row = np.max(np.where(posrows)[0])
            min_col = np.min(np.where(poscols)[0])
            max_col = np.max(np.where(poscols)[0])

            # if the min/max cols are within the image bounds,
            # add the mask, as 4 points, to the dictionary
            if min_col > 0 and max_col < image_cols:
                channel_masks_1fov[channel_id] = [[min_row, max_row], [min_col, max_col]]

                # find the largest channel width and height while you go round
                max_chnl_mask_len = int(max(max_chnl_mask_len, max_row - min_row))
                max_chnl_mask_wid = int(max(max_chnl_mask_wid, max_col - min_col))

        # add channel_mask dictionary to the fov dictionary, use copy to play it safe
        channel_masks[fov] = channel_masks_1fov.copy()

    # update all channel masks to be the max size
    cm_copy = channel_masks.copy()

    for fov, peaks in six.iteritems(channel_masks):
        # f_id = int(fov)
        for peak, chnl_mask in six.iteritems(peaks):
            # p_id = int(peak)
            # just add length to the open end (bottom of image, low column)
            if chnl_mask[0][1] - chnl_mask[0][0] !=  max_chnl_mask_len:
                cm_copy[fov][peak][0][1] = chnl_mask[0][0] + max_chnl_mask_len
            # enlarge widths around the middle, but make sure you don't get floats
            if chnl_mask[1][1] - chnl_mask[1][0] != max_chnl_mask_wid:
                wid_diff = max_chnl_mask_wid - (chnl_mask[1][1] - chnl_mask[1][0])
                if wid_diff % 2 == 0:
                    cm_copy[fov][peak][1][0] = max(chnl_mask[1][0] - wid_diff/2, 0)
                    cm_copy[fov][peak][1][1] = min(chnl_mask[1][1] + wid_diff/2, image_cols - 1)
                else:
                    cm_copy[fov][peak][1][0] = max(chnl_mask[1][0] - (wid_diff-1)/2, 0)
                    cm_copy[fov][peak][1][1] = min(chnl_mask[1][1] + (wid_diff+1)/2, image_cols - 1)

            # convert all values to ints
            chnl_mask[0][0] = int(chnl_mask[0][0])
            chnl_mask[0][1] = int(chnl_mask[0][1])
            chnl_mask[1][0] = int(chnl_mask[1][0])
            chnl_mask[1][1] = int(chnl_mask[1][1])

            # cm_copy[fov][peak] = {'y_top': chnl_mask[0][0],
            #                       'y_bot': chnl_mask[0][1],
            #                       'x_left': chnl_mask[1][0],
            #                       'x_right': chnl_mask[1][1]}
            # print(type(cm_copy[fov][peak][1][0]), cm_copy[fov][peak][1][0])

    #save the channel mask dictionary to a pickle and a text file
    # with open(os.path.join(params['ana_dir'], 'channel_masks.pkl'), 'wb') as cmask_file:
    #     pickle.dump(cm_copy, cmask_file, protocol=pickle.HIGHEST_PROTOCOL)
    with open(os.path.join(params['ana_dir'], 'channel_masks.txt'), 'w') as cmask_file:
        pprint(cm_copy, stream=cmask_file)
    with open(os.path.join(params['ana_dir'], 'channel_masks.yaml'), 'w') as cmask_file:
        yaml.dump(data=cm_copy, stream=cmask_file, default_flow_style=False, tags=None)

    information("Channel masks saved.")

    return cm_copy

### functions about trimming, padding, and manipulating images

# define function for flipping the images on an FOV by FOV basis
def fix_orientation(image_data):
    '''
    Fix the orientation. The standard direction for channels to open to is down.

    called by
    process_tif
    get_params
    '''

    # user parameter indicates how things should be flipped
    image_orientation = params['compile']['image_orientation']

    # if this is just a phase image give in an extra layer so rest of code is fine
    flat = False # flag for if the image is flat or multiple levels
    if len(image_data.shape) == 2:
        image_data = np.expand_dims(image_data, 0)
        flat = True

    # setting image_orientation to 'auto' will use autodetection
    if image_orientation == "auto":
         # use 'phase_plane' to find the phase plane in image_data, assuming c1, c2, c3... naming scheme here.
        try:
            ph_channel = int(re.search('[0-9]', params['phase_plane']).group(0)) - 1
        except:
            # Pick the plane to analyze with the highest mean px value (should be phase)
            ph_channel = np.argmax([np.mean(image_data[ci]) for ci in range(image_data.shape[0])])

        # flip based on the index of the higest average row value
        # this should be closer to the opening
        if np.argmax(image_data[ph_channel].mean(axis = 1)) < image_data[ph_channel].shape[0] / 2:
            image_data = image_data[:,::-1,:]
        else:
            pass # no need to do anything

    # flip if up is chosen
    elif image_orientation == "up":
        return image_data[:,::-1,:]

    # do not flip the images if "down is the specified image orientation"
    elif image_orientation == "down":
        pass

    if flat:
        image_data = image_data[0] # just return that first layer

    return image_data

# cuts out channels from the image
def cut_slice(image_data, channel_loc):
    '''Takes an image and cuts out the channel based on the slice location
    slice location is the list with the peak information, in the form
    [][y1, y2],[x1, x2]]. Returns the channel slice as a numpy array.
    The numpy array will be a stack if there are multiple planes.

    if you want to slice all the channels from a picture with the channel_masks
    dictionary use a loop like this:

    for channel_loc in channel_masks[fov_id]: # fov_id is the fov of the image
        channel_slice = cut_slice[image_pixel_data, channel_loc]
        # ... do something with the slice

    NOTE: this function will try to determine what the shape of your
    image is and slice accordingly. It expects the images are in the order
    [t, x, y, c]. It assumes images with three dimensions are [x, y, c] not
    [t, x, y].
    '''

    # case where image is in form [x, y]
    if len(image_data.shape) == 2:
        # make slice object
        channel_slicer = np.s_[channel_loc[0][0]:channel_loc[0][1],
                               channel_loc[1][0]:channel_loc[1][1]]

    # case where image is in form [x, y, c]
    elif len(image_data.shape) == 3:
        channel_slicer = np.s_[channel_loc[0][0]:channel_loc[0][1],
                               channel_loc[1][0]:channel_loc[1][1],:]

    # case where image in form [t, x , y, c]
    elif len(image_data.shape) == 4:
        channel_slicer = np.s_[:,channel_loc[0][0]:channel_loc[0][1],
                                 channel_loc[1][0]:channel_loc[1][1],:]

    # slice based on appropriate slicer object.
    channel_slice = image_data[channel_slicer]

    # pad y of channel if slice happened to be outside of image
    y_difference  = (channel_loc[0][1] - channel_loc[0][0]) - channel_slice.shape[1]
    if y_difference > 0:
        paddings = [[0, 0], # t
                    [0, y_difference], # y
                    [0, 0], # x
                    [0, 0]] # c
        channel_slice = np.pad(channel_slice, paddings, mode='edge')

    return channel_slice

# calculate cross correlation between pixels in channel stack
def channel_xcorr(fov_id, peak_id):
    '''
    Function calculates the cross correlation of images in a
    stack to the first image in the stack. The output is an
    array that is the length of the stack with the best cross
    correlation between that image and the first image.

    The very first value should be 1.
    '''

    pad_size = params['subtract']['alignment_pad']

    # Use this number of images to calculate cross correlations
    number_of_images = 20

    # load the phase contrast images
    image_data = load_stack(fov_id, peak_id, color=params['phase_plane'])

    # if there are more images than number_of_images, use number_of_images images evenly
    # spaced across the range
    if image_data.shape[0] > number_of_images:
        spacing = int(image_data.shape[0] / number_of_images)
        image_data = image_data[::spacing,:,:]
        if image_data.shape[0] > number_of_images:
            image_data = image_data[:number_of_images,:,:]

    # we will compare all images to this one, needs to be padded to account for image drift
    first_img = np.pad(image_data[0,:,:], pad_size, mode='reflect')

    xcorr_array = [] # array holds cross correlation vaues
    for img in image_data:
        # use match_template to find all cross correlations for the
        # current image against the first image.
        xcorr_array.append(np.max(match_template(first_img, img)))

    return xcorr_array

### functions about subtraction

# average empty channels from stacks, making another TIFF stack
def average_empties_stack(fov_id, specs, color='c1', align=True):
    '''Takes the fov file name and the peak names of the designated empties,
    averages them and saves the image

    Parameters
    fov_id : int
        FOV number
    specs : dict
        specifies whether a channel should be analyzed (1), used for making
        an average empty (0), or ignored (-1).
    color : string
        Which plane to use.
    align : boolean
        Flag that is passed to the worker function average_empties, indicates
        whether images should be aligned be for averaging (use False for fluorescent images)

    Returns
        True if succesful.
        Saves empty stack to analysis folder

    '''

    information("Creating average empty channel for FOV %d." % fov_id)

    # get peak ids of empty channels for this fov
    empty_peak_ids = []
    for peak_id, spec in six.iteritems(specs[fov_id]):
        if spec == 0: # 0 means it should be used for empty
            empty_peak_ids.append(peak_id)
    empty_peak_ids = sorted(empty_peak_ids) # sort for repeatability

    # depending on how many empties there are choose what to do
    # if there is no empty the user is going to have to copy another empty stack
    if len(empty_peak_ids) == 0:
        information("No empty channel designated for FOV %d." % fov_id)
        return False

    # if there is just one then you can just copy that channel
    elif len(empty_peak_ids) == 1:
        peak_id = empty_peak_ids[0]
        information("One empty channel (%d) designated for FOV %d." % (peak_id, fov_id))

        # load the one phase contrast as the empties
        avg_empty_stack = load_stack(fov_id, peak_id, color=color)

    # but if there is more than one empty you need to align and average them per timepoint
    elif len(empty_peak_ids) > 1:
        # load the image stacks into memory
        empty_stacks = [] # list which holds phase image stacks of designated empties
        for peak_id in empty_peak_ids:
            # load data and append to list
            image_data = load_stack(fov_id, peak_id, color=color)

            empty_stacks.append(image_data)

        information("%d empty channels designated for FOV %d." % (len(empty_stacks), fov_id))

        # go through time points and create list of averaged empties
        avg_empty_stack = [] # list will be later concatentated into numpy array
        time_points = range(image_data.shape[0]) # index is time
        for t in time_points:
            # get images from one timepoint at a time and send to alignment and averaging
            imgs = [stack[t] for stack in empty_stacks]
            avg_empty = average_empties(imgs, align=align) # function is in mm3
            avg_empty_stack.append(avg_empty)

        # concatenate list and then save out to tiff stack
        avg_empty_stack = np.stack(avg_empty_stack, axis=0)

    # save out data
    if params['output'] == 'TIFF':
        # make new name and save it
        empty_filename = params['experiment_name'] + '_xy%03d_empty_%s.tif' % (fov_id, color)
        tiff.imsave(os.path.join(params['empty_dir'],empty_filename), avg_empty_stack, compress=4)

    if params['output'] == 'HDF5':
        h5f = h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'r+')

        # delete the dataset if it exists (important for debug)
        if 'empty_%s' % color in h5f:
            del h5f[u'empty_%s' % color]

        # the empty channel should be it's own dataset
        h5ds = h5f.create_dataset(u'empty_%s' % color,
                        data=avg_empty_stack,
                        chunks=(1, avg_empty_stack.shape[1], avg_empty_stack.shape[2]),
                        maxshape=(None, avg_empty_stack.shape[1], avg_empty_stack.shape[2]),
                        compression="gzip", shuffle=True, fletcher32=True)

        # give attribute which says which channels contribute
        h5ds.attrs.create('empty_channels', empty_peak_ids)
        h5f.close()

    information("Saved empty channel for FOV %d." % fov_id)

    return True

# averages a list of empty channels
def average_empties(imgs, align=True):
    '''
    This function averages a set of images (empty channels) and returns a single image
    of the same size. It first aligns the images to the first image before averaging.

    Alignment is done by enlarging the first image using edge padding.
    Subsequent images are then aligned to this image and the offset recorded.
    These images are padded such that they are the same size as the first (padded) image but
    with the image in the correct (aligned) place. Edge padding is again used.
    The images are then placed in a stack and aveaged. This image is trimmed so it is the size
    of the original images

    Called by
    average_empties_stack
    '''

    aligned_imgs = [] # list contains the aligned, padded images

    if align:
        # pixel size to use for padding (ammount that alignment could be off)
        pad_size = params['subtract']['alignment_pad']

        for n, img in enumerate(imgs):
            # if this is the first image, pad it and add it to the stack
            if n == 0:
                ref_img = np.pad(img, pad_size, mode='reflect') # padded reference image
                aligned_imgs.append(ref_img)

            # otherwise align this image to the first padded image
            else:
                # find correlation between a convolution of img against the padded reference
                match_result = match_template(ref_img, img)

                # find index of highest correlation (relative to top left corner of img)
                y, x = np.unravel_index(np.argmax(match_result), match_result.shape)

                # pad img so it aligns and is the same size as reference image
                pad_img = np.pad(img, ((y, ref_img.shape[0] - (y + img.shape[0])),
                                       (x, ref_img.shape[1] - (x + img.shape[1]))), mode='reflect')
                aligned_imgs.append(pad_img)
    else:
        # don't align, just link the names to go forward easily
        aligned_imgs = imgs

    # stack the aligned data along 3rd axis
    aligned_imgs = np.dstack(aligned_imgs)
    # get a mean image along 3rd axis
    avg_empty = np.nanmean(aligned_imgs, axis=2)
    # trim off the padded edges (only if images were alinged, otherwise there was no padding)
    if align:
        avg_empty = avg_empty[pad_size:-1*pad_size, pad_size:-1*pad_size]
    # change type back to unsigned 16 bit not floats
    avg_empty = avg_empty.astype(dtype='uint16')

    return avg_empty

# this function is used when one FOV doesn't have an empty
def copy_empty_stack(from_fov, to_fov, color='c1'):
    '''Copy an empty stack from one FOV to another'''

    # load empty stack from one FOV
    information('Loading empty stack from FOV {} to save for FOV {}.'.format(from_fov, to_fov))
    avg_empty_stack = load_stack(from_fov, 0, color='empty_{}'.format(color))

    # save out data
    if params['output'] == 'TIFF':
        # make new name and save it
        empty_filename = params['experiment_name'] + '_xy%03d_empty_%s.tif' % (to_fov, color)
        tiff.imsave(os.path.join(params['empty_dir'],empty_filename), avg_empty_stack, compress=4)

    if params['output'] == 'HDF5':
        h5f = h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % to_fov), 'r+')

        # delete the dataset if it exists (important for debug)
        if 'empty_%s' % color in h5f:
            del h5f[u'empty_%s' % color]

        # the empty channel should be it's own dataset
        h5ds = h5f.create_dataset(u'empty_%s' % color,
                        data=avg_empty_stack,
                        chunks=(1, avg_empty_stack.shape[1], avg_empty_stack.shape[2]),
                        maxshape=(None, avg_empty_stack.shape[1], avg_empty_stack.shape[2]),
                        compression="gzip", shuffle=True, fletcher32=True)

        # give attribute which says which channels contribute. Just put 0
        h5ds.attrs.create('empty_channels', [0])
        h5f.close()

    information("Saved empty channel for FOV %d." % to_fov)

# Do subtraction for an fov over many timepoints
def subtract_fov_stack(fov_id, specs, color='c1', method='phase'):
    '''
    For a given FOV, loads the precomputed empty stack and does subtraction on
    all peaks in the FOV designated to be analyzed

    Parameters
    ----------
    color : string, 'c1', 'c2', etc.
        This is the channel to subtraction. will be appended to the word empty.

    Called by
    mm3_Subtract.py

    Calls
    mm3.subtract_phase

    '''

    information('Subtracting peaks for FOV %d.' % fov_id)

    # load empty stack feed dummy peak number to get empty
    avg_empty_stack = load_stack(fov_id, 0, color='empty_{}'.format(color))

    # determine which peaks are to be analyzed
    ana_peak_ids = []
    for peak_id, spec in six.iteritems(specs[fov_id]):
        if spec == 1: # 0 means it should be used for empty, -1 is ignore
            ana_peak_ids.append(peak_id)
    ana_peak_ids = sorted(ana_peak_ids) # sort for repeatability
    information("Subtracting %d channels for FOV %d." % (len(ana_peak_ids), fov_id))

    # just break if there are to peaks to analize
    if not ana_peak_ids:
        return False

    # load images for the peak and get phase images
    for peak_id in ana_peak_ids:
        information('Subtracting peak %d.' % peak_id)

        image_data = load_stack(fov_id, peak_id, color=color)

        # make a list for all time points to send to a multiprocessing pool
        # list will length of image_data with tuples (image, empty)
        subtract_pairs = zip(image_data, avg_empty_stack)

        # set up multiprocessing pool to do subtraction. Should wait until finished
        pool = Pool(processes=params['num_analyzers'])

        if method == 'phase':
            subtracted_imgs = pool.map(subtract_phase, subtract_pairs, chunksize=10)
        elif method == 'fluor':
            subtracted_imgs = pool.map(subtract_fluor, subtract_pairs, chunksize=10)

        pool.close() # tells the process nothing more will be added.
        pool.join() # blocks script until everything has been processed and workers exit

        # linear loop for debug
        # subtracted_imgs = [subtract_phase(subtract_pair) for subtract_pair in subtract_pairs]

        # stack them up along a time axis
        subtracted_stack = np.stack(subtracted_imgs, axis=0)

        # save out the subtracted stack
        if params['output'] == 'TIFF':
            sub_filename = params['experiment_name'] + '_xy%03d_p%04d_sub_%s.tif' % (fov_id, peak_id, color)
            tiff.imsave(os.path.join(params['sub_dir'],sub_filename), subtracted_stack, compress=4) # save it

            if fov_id==1 and peak_id<50:
                napari.current_viewer().add_image(subtracted_stack, name='Subtracted' + '_xy1_p'+str(peak_id)+'_sub_'+str(color)+'.tif', visible=True)

        if params['output'] == 'HDF5':
            h5f = h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'r+')

            # put subtracted channel in correct group
            h5g = h5f['channel_%04d' % peak_id]

            # delete the dataset if it exists (important for debug)
            if 'p%04d_sub_%s' % (peak_id, color) in h5g:
                del h5g['p%04d_sub_%s' % (peak_id, color)]

            h5ds = h5g.create_dataset(u'p%04d_sub_%s' % (peak_id, color),
                            data=subtracted_stack,
                            chunks=(1, subtracted_stack.shape[1], subtracted_stack.shape[2]),
                            maxshape=(None, subtracted_stack.shape[1], subtracted_stack.shape[2]),
                            compression="gzip", shuffle=True, fletcher32=True)

        information("Saved subtracted channel %d." % peak_id)

    if params['output'] == 'HDF5':
        h5f.close()

    return True

# subtracts one phase contrast image from another.
def subtract_phase(image_pair):
    '''subtract_phase aligns and subtracts a .
    Modified from subtract_phase_only by jt on 20160511
    The subtracted image returned is the same size as the image given. It may however include
    data points around the edge that are meaningless but not marked.

    We align the empty channel to the phase channel, then subtract.

    Parameters
    image_pair : tuple of length two with; (image, empty_mean)

    Returns
    channel_subtracted : np.array
        The subtracted image

    Called by
    subtract_fov_stack
    '''
    # get out data and pad
    cropped_channel, empty_channel = image_pair # [channel slice, empty slice]

    # this is for aligning the empty channel to the cell channel.
    ### Pad cropped channel.
    pad_size = params['subtract']['alignment_pad'] # pixel size to use for padding (ammount that alignment could be off)
    padded_chnl = np.pad(cropped_channel, pad_size, mode='reflect')

    # ### Align channel to empty using match template.
    # use match template to get a correlation array and find the position of maximum overlap
    match_result = match_template(padded_chnl, empty_channel)
    # get row and colum of max correlation value in correlation array
    y, x = np.unravel_index(np.argmax(match_result), match_result.shape)

    # pad the empty channel according to alignment to be overlayed on padded channel.
    empty_paddings = [[y, padded_chnl.shape[0] - (y + empty_channel.shape[0])],
                      [x, padded_chnl.shape[1] - (x + empty_channel.shape[1])]]
    aligned_empty = np.pad(empty_channel, empty_paddings, mode='reflect')
    # now trim it off so it is the same size as the original channel
    aligned_empty = aligned_empty[pad_size:-1*pad_size, pad_size:-1*pad_size]

    ### Compute the difference between the empty and channel phase contrast images
    # subtract cropped cell image from empty channel.
    channel_subtracted = aligned_empty.astype('int32') - cropped_channel.astype('int32')
    # channel_subtracted = cropped_channel.astype('int32') - aligned_empty.astype('int32')

    # just zero out anything less than 0. This is what Sattar does
    channel_subtracted[channel_subtracted < 0] = 0
    channel_subtracted = channel_subtracted.astype('uint16') # change back to 16bit

    return channel_subtracted

# subtract one fluorescence image from another.
def subtract_fluor(image_pair):
    ''' subtract_fluor does a simple subtraction of one image to another. Unlike subtract_phase,
    there is no alignment. Also, the empty channel is subtracted from the full channel.

    Parameters
    image_pair : tuple of length two with; (image, empty_mean)

    Returns
    channel_subtracted : np.array
        The subtracted image.

    Called by
    subtract_fov_stack
    '''
    # get out data and pad
    cropped_channel, empty_channel = image_pair # [channel slice, empty slice]

    # check frame size of cropped channel and background, always keep crop channel size the same
    crop_size = np.shape(cropped_channel)[:2]
    empty_size = np.shape(empty_channel)[:2]
    if crop_size != empty_size:
        if crop_size[0] > empty_size[0] or crop_size[1] > empty_size[1]:
            pad_row_length = max(crop_size[0]  - empty_size[0], 0) # prevent negatives
            pad_column_length = max(crop_size[1]  - empty_size[1], 0)
            empty_channel = np.pad(empty_channel,
                [[np.int(.5*pad_row_length), pad_row_length-np.int(.5*pad_row_length)],
                [np.int(.5*pad_column_length),  pad_column_length-np.int(.5*pad_column_length)],
                [0,0]], 'edge')
            # mm3.information('size adjusted 1')
        empty_size = np.shape(empty_channel)[:2]
        if crop_size[0] < empty_size[0] or crop_size[1] < empty_size[1]:
            empty_channel = empty_channel[:crop_size[0], :crop_size[1],]

    ### Compute the difference between the empty and channel phase contrast images
    # subtract cropped cell image from empty channel.
    channel_subtracted = cropped_channel.astype('int32') - empty_channel.astype('int32')
    # channel_subtracted = cropped_channel.astype('int32') - aligned_empty.astype('int32')

    # just zero out anything less than 0.
    channel_subtracted[channel_subtracted < 0] = 0
    channel_subtracted = channel_subtracted.astype('uint16') # change back to 16bit

    return channel_subtracted

### functions that deal with segmentation and lineages

# Do segmentation for an channel time stack
def segment_chnl_stack(fov_id, peak_id):
    '''
    For a given fov and peak (channel), do segmentation for all images in the
    subtracted .tif stack.

    Called by
    mm3_Segment.py

    Calls
    mm3.segment_image
    '''

    information('Segmenting FOV %d, channel %d.' % (fov_id, peak_id))

    # load subtracted images
    sub_stack = load_stack(fov_id, peak_id, color='sub_{}'.format(params['phase_plane']))

    # # set up multiprocessing pool to do segmentation. Will do everything before going on.
    # pool = Pool(processes=params['num_analyzers'])

    # # send the 3d array to multiprocessing
    # segmented_imgs = pool.map(segment_image, sub_stack, chunksize=8)

    # pool.close() # tells the process nothing more will be added.
    # pool.join() # blocks script until everything has been processed and workers exit

    # image by image for debug
    segmented_imgs = []
    for sub_image in sub_stack:
        segmented_imgs.append(segment_image(sub_image))

    # stack them up along a time axis
    segmented_imgs = np.stack(segmented_imgs, axis=0)
    segmented_imgs = segmented_imgs.astype('uint8')

    # save out the segmented stack
    if params['output'] == 'TIFF':
        seg_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, params['seg_img'])
        tiff.imsave(os.path.join(params['seg_dir'],seg_filename),
                    segmented_imgs, compress=5)
        if fov_id==1 and peak_id<50:
            napari.current_viewer().add_image(segmented_imgs, name='Segmented' + '_xy1_p'+str(peak_id)+'_'+str(params['seg_img'])+'.tif', visible=True)

    if params['output'] == 'HDF5':
        h5f = h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'r+')

        # put segmented channel in correct group
        h5g = h5f['channel_%04d' % peak_id]

        # delete the dataset if it exists (important for debug)
        if 'p%04d_%s' % (peak_id, params['seg_img']) in h5g:
            del h5g['p%04d_%s' % (peak_id, params['seg_img'])]

        h5ds = h5g.create_dataset(u'p%04d_%s' % (peak_id, params['seg_img']),
                        data=segmented_imgs,
                        chunks=(1, segmented_imgs.shape[1], segmented_imgs.shape[2]),
                        maxshape=(None, segmented_imgs.shape[1], segmented_imgs.shape[2]),
                        compression="gzip", shuffle=True, fletcher32=True)
        h5f.close()

    information("Saved segmented channel %d." % peak_id)

    return True

# segmentation algorithm
def segment_image(image):
    '''Segments a subtracted image and returns a labeled image

    Parameters
    image : a ndarray which is an image. This should be the subtracted image

    Returns
    labeled_image : a ndarray which is also an image. Labeled values, which
        should correspond to cells, all have the same integer value starting with 1.
        Non labeled area should have value zero.
    '''

    # load in segmentation parameters
    OTSU_threshold = params['segment']['OTSU_threshold']
    first_opening_size = params['segment']['first_opening_size']
    distance_threshold = params['segment']['distance_threshold']
    second_opening_size = params['segment']['second_opening_size']
    min_object_size = params['segment']['min_object_size']

    # threshold image
    try:
        thresh = threshold_otsu(image) # finds optimal OTSU threshhold value
    except:
        return np.zeros_like(image)

    threshholded = image > OTSU_threshold*thresh # will create binary image

    # if there are no cells, good to clear the border
    # because otherwise the OTSU is just for random bullshit, most
    # likely on the side of the image
    threshholded = segmentation.clear_border(threshholded)

    # Opening = erosion then dialation.
    # opening smooths images, breaks isthmuses, and eliminates protrusions.
    # "opens" dark gaps between bright features.
    morph = morphology.binary_opening(threshholded, morphology.disk(first_opening_size))

    # if this image is empty at this point (likely if there were no cells), just return
    # zero array
    if np.amax(morph) == 0:
        return np.zeros_like(image)

    ### Calculate distance matrix, use as markers for random walker (diffusion watershed)
    # Generate the markers based on distance to the background
    distance = ndi.distance_transform_edt(morph)

    # threshold distance image
    distance_thresh = np.zeros_like(distance)
    distance_thresh[distance < distance_threshold] = 0
    distance_thresh[distance >= distance_threshold] = 1

    # do an extra opening on the distance
    distance_opened = morphology.binary_opening(distance_thresh,
                                                morphology.disk(second_opening_size))

    # remove artifacts connected to image border
    cleared = segmentation.clear_border(distance_opened)
    # remove small objects. Remove small objects wants a
    # labeled image and will fail if there is only one label. Return zero image in that case
    # could have used try/except but remove_small_objects loves to issue warnings.
    cleared, label_num = morphology.label(cleared, connectivity=1, return_num=True)
    if label_num > 1:
        cleared = morphology.remove_small_objects(cleared, min_size=min_object_size)
    else:
        # if there are no labels, then just return the cleared image as it is zero
        return np.zeros_like(image)

    # relabel now that small objects and labels on edges have been cleared
    markers = morphology.label(cleared, connectivity=1)

    # just break if there is no label
    if np.amax(markers) == 0:
        return np.zeros_like(image)

    # the binary image for the watershed, which uses the unmodified OTSU threshold
    threshholded_watershed = threshholded
    threshholded_watershed = segmentation.clear_border(threshholded_watershed)

    # label using the random walker (diffusion watershed) algorithm
    try:
        # set anything outside of OTSU threshold to -1 so it will not be labeled
        markers[threshholded_watershed == 0] = -1
        # here is the main algorithm
        labeled_image = segmentation.random_walker(-1*image, markers)
        # put negative values back to zero for proper image
        labeled_image[labeled_image == -1] = 0
    except:
        return np.zeros_like(image)

    return labeled_image

# loss functions for model
def dice_coeff(y_true, y_pred):
    smooth = 1.
    # Flatten
    y_true_f = tf.reshape(y_true, [-1])
    y_pred_f = tf.reshape(y_pred, [-1])
    intersection = tf.reduce_sum(y_true_f * y_pred_f)
    score = (2. * intersection + smooth) / (tf.reduce_sum(y_true_f) + tf.reduce_sum(y_pred_f) + smooth)
    return score

def dice_loss(y_true, y_pred):
    loss = 1 - dice_coeff(y_true, y_pred)
    return loss

def bce_dice_loss(y_true, y_pred):
    loss = losses.binary_crossentropy(y_true, y_pred) + dice_loss(y_true, y_pred)
    return loss

def tversky_loss(y_true, y_pred):
    alpha = 0.5
    beta  = 0.5

    ones = K.ones((512,512,3)) #K.ones(K.shape(y_true))
    p0 = y_pred      # proba that voxels are class i
    p1 = ones-y_pred # proba that voxels are not class i
    g0 = y_true
    g1 = ones-y_true

    num = K.sum(p0*g0, (0,1,2))
    den = num + alpha*K.sum(p0*g1,(0,1,2)) + beta*K.sum(p1*g0,(0,1,2))

    T = K.sum(num/den) # when summing over classes, T has dynamic range [0 Ncl]

    Ncl = K.cast(K.shape(y_true)[-1], 'float32')
    return Ncl-T

def cce_tversky_loss(y_true, y_pred):
    loss = losses.categorical_crossentropy(y_true, y_pred) + tversky_loss(y_true, y_pred)
    return loss

def get_pad_distances(unet_shape, img_height, img_width):
    '''Finds padding and trimming sizes to make the input image the same as the size expected by the U-net model.

    Padding is done evenly to the top and bottom of the image. Trimming is only done from the right or bottom.
    '''

    half_width_pad = (unet_shape[1]-img_width)/2
    if half_width_pad > 0:
        left_pad = int(np.floor(half_width_pad))
        right_pad = int(np.ceil(half_width_pad))
        right_trim = 0
    else:
        left_pad = 0
        right_pad = 0
        right_trim = img_width - unet_shape[1]

    half_height_pad = (unet_shape[0]-img_height)/2
    if half_height_pad > 0:
        top_pad = int(np.floor(half_height_pad))
        bottom_pad = int(np.ceil(half_height_pad))
        bottom_trim = 0
    else:
        top_pad = 0
        bottom_pad = 0
        bottom_trim = img_height - unet_shape[0]

    pad_dict = {'top_pad' : top_pad,
                'bottom_pad' : bottom_pad,
                'right_pad' : right_pad,
                'left_pad' : left_pad,
                'bottom_trim' : bottom_trim,
                'right_trim' : right_trim}

    return pad_dict

#@profile
def segment_cells_unet(ana_peak_ids, fov_id, pad_dict, unet_shape, model):

    batch_size = params['segment']['batch_size']
    cellClassThreshold = params['segment']['cell_class_threshold']
    if cellClassThreshold == 'None': # yaml imports None as a string
        cellClassThreshold = False
    min_object_size = params['segment']['min_object_size']

    # arguments to data generator
    # data_gen_args = {'batch_size':batch_size,
    #                  'n_channels':1,
    #                  'normalize_to_one':False,
    #                  'shuffle':False}

    #arguments to predict
    predict_args = dict(use_multiprocessing=True,
                        workers=params['num_analyzers'],
                        verbose=1)

    # predict_args = dict(use_multiprocessing=False,
    #                     verbose=1)

    for peak_id in ana_peak_ids:
        information('Segmenting peak {}.'.format(peak_id))

        img_stack = load_stack(fov_id, peak_id, color=params['phase_plane'])

        if params['segment']['normalize_to_one']:
            med_stack = np.zeros(img_stack.shape)
            selem = morphology.disk(1)

            for frame_idx in range(img_stack.shape[0]):
                tmpImg = img_stack[frame_idx,...]
                med_stack[frame_idx,...] = median(tmpImg, selem)

            # robust normalization of peak's image stack to 1
            max_val = np.max(med_stack)
            img_stack = img_stack/max_val
            img_stack[img_stack > 1] = 1

        # trim and pad image to correct size
        img_stack = img_stack[:, :unet_shape[0], :unet_shape[1]]
        img_stack = np.pad(img_stack,
                           ((0,0),
                           (pad_dict['top_pad'],pad_dict['bottom_pad']),
                           (pad_dict['left_pad'],pad_dict['right_pad'])),
                           mode='constant')
        img_stack = np.expand_dims(img_stack, -1) # TF expects images to be 4D
        # set up image generator
        # image_generator = CellSegmentationDataGenerator(img_stack, **data_gen_args)
        image_datagen = ImageDataGenerator()
        image_generator = image_datagen.flow(x=img_stack,
                                             batch_size=batch_size,
                                             shuffle=False) # keep same order

        # predict cell locations. This has multiprocessing built in but I need to mess with the parameters to see how to best utilize it. ***
        predictions = model.predict(image_generator, **predict_args)

        # post processing
        # remove padding including the added last dimension
        predictions = predictions[:, pad_dict['top_pad']:unet_shape[0]-pad_dict['bottom_pad'],
                                     pad_dict['left_pad']:unet_shape[1]-pad_dict['right_pad'], 0]

        # pad back incase the image had been trimmed
        predictions = np.pad(predictions,
                             ((0,0),
                             (0,pad_dict['bottom_trim']),
                             (0,pad_dict['right_trim'])),
                             mode='constant')

        if params['segment']['save_predictions']:
            pred_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, params['pred_img'])
            if not os.path.isdir(params['pred_dir']):
                os.makedirs(params['pred_dir'])
            int_preds = (predictions * 255).astype('uint8')
            tiff.imsave(os.path.join(params['pred_dir'], pred_filename),
                            int_preds, compress=4)

        # binarized and label (if there is a threshold value, otherwise, save a grayscale for debug)
        if cellClassThreshold:
            predictions[predictions >= cellClassThreshold] = 1
            predictions[predictions < cellClassThreshold] = 0
            predictions = predictions.astype('uint8')

            segmented_imgs = np.zeros(predictions.shape, dtype='uint8')
            # process and label each frame of the channel
            for frame in range(segmented_imgs.shape[0]):
                # get rid of small holes
                predictions[frame,:,:] = morphology.remove_small_holes(predictions[frame,:,:], min_object_size)
                # get rid of small objects.
                predictions[frame,:,:] = morphology.remove_small_objects(morphology.label(predictions[frame,:,:], connectivity=1), min_size=min_object_size)
                # remove labels which touch the boarder
                predictions[frame,:,:] = segmentation.clear_border(predictions[frame,:,:])
                # relabel now
                segmented_imgs[frame,:,:] = morphology.label(predictions[frame,:,:], connectivity=1)

        else: # in this case you just want to scale the 0 to 1 float image to 0 to 255
            information('Converting predictions to grayscale.')
            segmented_imgs = np.around(predictions * 100)

        # both binary and grayscale should be 8bit. This may be ensured above and is unneccesary
        segmented_imgs = segmented_imgs.astype('uint8')

        # save out the segmented stacks
        if params['output'] == 'TIFF':
            seg_filename = params['experiment_name'] + '_xy%03d_p%04d_%s.tif' % (fov_id, peak_id, params['seg_img'])
            tiff.imsave(os.path.join(params['seg_dir'], seg_filename),
                            segmented_imgs, compress=4)

            if fov_id==1 and peak_id<50:
                napari.current_viewer().add_image(segmented_imgs, name='Segmented' + '_xy1_p'+str(peak_id)+'_'+str(params['seg_img'])+'.tif', visible=True)

        if params['output'] == 'HDF5':
            h5f = h5py.File(os.path.join(params['hdf5_dir'],'xy%03d.hdf5' % fov_id), 'r+')
            # put segmented channel in correct group
            h5g = h5f['channel_%04d' % peak_id]
            # delete the dataset if it exists (important for debug)
            if 'p%04d_%s' % (peak_id, params['seg_img']) in h5g:
                del h5g['p%04d_%s' % (peak_id, params['seg_img'])]

            h5ds = h5g.create_dataset(u'p%04d_%s' % (peak_id, params['seg_img']),
                                data=segmented_imgs,
                                chunks=(1, segmented_imgs.shape[1], segmented_imgs.shape[2]),
                                maxshape=(None, segmented_imgs.shape[1], segmented_imgs.shape[2]),
                                compression="gzip", shuffle=True, fletcher32=True)
            h5f.close()

#@profile
def segment_fov_unet(fov_id, specs, model, color=None):
    '''
    Segments the channels from one fov using the U-net CNN model.

    Parameters
    ----------
    fov_id : int
    specs : dict
    model : TensorFlow model
    '''

    information('Segmenting FOV {} with U-net.'.format(fov_id))

    if color is None:
        color = params['phase_plane']

    # load segmentation parameters
    unet_shape = (params['segment']['trained_model_image_height'],
                  params['segment']['trained_model_image_width'])

    ### determine stitching of images.
    # need channel shape, specifically the width. load first for example
    # this assumes that all channels are the same size for this FOV, which they should
    for peak_id, spec in six.iteritems(specs[fov_id]):
        if spec == 1:
            break # just break out with the current peak_id

    img_stack = load_stack(fov_id, peak_id, color=color)
    img_height = img_stack.shape[1]
    img_width = img_stack.shape[2]

    pad_dict = get_pad_distances(unet_shape, img_height, img_width)

    # dermine how many channels we have to analyze for this FOV
    ana_peak_ids = []
    for peak_id, spec in six.iteritems(specs[fov_id]):
        if spec == 1:
            ana_peak_ids.append(peak_id)
    ana_peak_ids.sort() # sort for repeatability
    #ana_peak_ids = ana_peak_ids[:2]

    segment_cells_unet(ana_peak_ids, fov_id, pad_dict, unet_shape, model)

    information("Finished segmentation for FOV {}.".format(fov_id))

    return

# finds lineages for all peaks in a fov
def make_lineages_fov(fov_id, specs):
    '''
    For a given fov, create the lineages from the segmented images.

    Called by
    mm3_Segment.py

    Calls
    mm3.make_lineage_chnl_stack
    '''
    ana_peak_ids = [] # channels to be analyzed
    for peak_id, spec in six.iteritems(specs[fov_id]):
        if spec == 1: # 1 means analyze
            ana_peak_ids.append(peak_id)
    ana_peak_ids = sorted(ana_peak_ids) # sort for repeatability

    information('Creating lineage for FOV %d with %d channels.' % (fov_id, len(ana_peak_ids)))

    # just break if there are no peaks to analize
    if not ana_peak_ids:
        # returning empty dictionary will add nothing to current cells dictionary
        return {}

    # This is a list of tuples (fov_id, peak_id) to send to the Pool command
    fov_and_peak_ids_list = [(fov_id, peak_id) for peak_id in ana_peak_ids]

    # set up multiprocessing pool. will complete pool before going on
    pool = Pool(processes=params['num_analyzers'])

    # create the lineages for each peak individually
    # the output is a list of dictionaries
    lineages = pool.map(make_lineage_chnl_stack, fov_and_peak_ids_list, chunksize=8)

    pool.close() # tells the process nothing more will be added.
    pool.join() # blocks script until everything has been processed and workers exit

    # This is the non-parallelized version (useful for debug)
    # lineages = []
    # for fov_and_peak_ids in fov_and_peak_ids_list:
    #     lineages.append(make_lineage_chnl_stack(fov_and_peak_ids))

    # combine all dictionaries into one dictionary
    Cells = {} # create dictionary to hold all information
    for cell_dict in lineages: # for all the other dictionaries in the list
        Cells.update(cell_dict) # updates Cells with the entries in cell_dict

    return Cells

# get number of cells in each frame and total number of pairwise interactions
def get_cell_counts(regionprops_list):

    cell_count_list = [len(time_regions) for time_regions in regionprops_list]
    interaction_count_list = []

    for i,cell_count in enumerate(cell_count_list):
        if i+1 == len(cell_count_list):
            break
        interaction_count_list.append(cell_count*cell_count_list[i+1])

    total_cells = np.sum(cell_count_list)
    total_interactions = np.sum(interaction_count_list)

    return(total_cells, total_interactions, cell_count_list, interaction_count_list)

# get cells' information for track prediction
def gather_interactions_and_events(regionprops_list):

    total_cells, total_interactions, cell_count_list, interaction_count_list = get_cell_counts(regionprops_list)

    # instantiate an array with a 2x4 array for each pair of cells'
    #   min_y, max_y, centroid_y, and area
    # in reality it would be much, much more efficient to
    #   look this information up in the data generator at run time
    #   for now, this will work
    pairwise_cell_data = np.zeros((total_interactions,2,5,1))

    # make a dictionary, the keys of which will be row indices so that we
    #   can quickly look up which timepoints/cells correspond to which
    #   rows of our model's ouput
    pairwise_cell_lookup = {}

    # populate arrays
    interaction_count = 0
    cell_count = 0

    for frame, frame_regions in enumerate(regionprops_list):

        for region in frame_regions:

            cell_label = region.label
            y,x = region.centroid
            bbox = region.bbox
            orientation = region.orientation
            min_y = bbox[0]
            max_y = bbox[2]
            area = region.area
            cell_label = region.label
            cell_info = (min_y, max_y, y, area, orientation)
            cell_count += 1

            try:
                frame_plus_one_regions = regionprops_list[frame+1]
            except IndexError as e:
                # print(e)
                break

            for region_plus_one in frame_plus_one_regions:

                paired_cell_label = region_plus_one.label
                y,x = region_plus_one.centroid
                bbox = region_plus_one.bbox
                min_y = bbox[0]
                max_y = bbox[2]
                area = region_plus_one.area
                paired_cell_label = region_plus_one.label

                pairwise_cell_data[interaction_count,0,:,0] = cell_info
                pairwise_cell_data[interaction_count,1,:,0] = (min_y, max_y, y, area, orientation)

                pairwise_cell_lookup[interaction_count] = {'frame':frame, 'cell_label':cell_label, 'paired_cell_label':paired_cell_label}

                interaction_count += 1

    return(pairwise_cell_data, pairwise_cell_lookup)

# look up which cells are interacting according to the track model
def cell_interaction_lookup(predictions, lookup_table):
    '''
    Accepts prediction matrix and
    '''
    frame = []
    cell_label = []
    paired_cell_label = []
    interaction_type = []

    # loop over rows of predictions
    for row_index in range(predictions.shape[0]):

        row_predictions = predictions[row_index]
        row_relationship = np.where(row_predictions > 0.95)[0]
        if row_relationship.size == 0:
            continue
        elif row_relationship[0] == 3:
            continue
        elif row_relationship[0] == 0:
            interaction_type.append('migration')
        elif row_relationship[0] == 1:
            interaction_type.append('child')
        elif row_relationship[0] == 2:
            interaction_type.append('false_join')

        frame.append(lookup_table[row_index]['frame'])
        cell_label.append(lookup_table[row_index]['cell_label'])
        paired_cell_label.append(lookup_table[row_index]['paired_cell_label'])

    track_df = pd.DataFrame(data={'frame':frame,
                              'cell_label':cell_label,
                              'paired_cell_label':paired_cell_label,
                              'interaction_type':interaction_type})
    return(track_df)

def get_tracking_model_dict():

    model_dict = {}

    if not 'migrate_model' in model_dict:
        model_dict['migrate_model'] = models.load_model(params['tracking']['migrate_model'],
                                                                    custom_objects={'all_loss':all_loss,
                                                                        'f2_m':f2_m})
    if not 'child_model' in model_dict:
        model_dict['child_model'] = models.load_model(params['tracking']['child_model'],
                                        custom_objects={'bce_dice_loss':bce_dice_loss,
                                                                    'f2_m':f2_m})
    if not 'appear_model' in model_dict:
        model_dict['appear_model'] = models.load_model(params['tracking']['appear_model'],
                                        custom_objects={'all_loss':all_loss,
                                                                    'f2_m':f2_m})
    if not 'die_model' in model_dict:
        model_dict['die_model'] = models.load_model(params['tracking']['die_model'],
                                        custom_objects={'all_loss':all_loss,
                                                                    'f2_m':f2_m})
    if not 'disappear_model' in model_dict:
        model_dict['disappear_model'] = models.load_model(params['tracking']['disappear_model'],
                                        custom_objects={'all_loss':all_loss,
                                                                    'f2_m':f2_m})
    if not 'born_model' in model_dict:
        model_dict['born_model'] = models.load_model(params['tracking']['born_model'],
                                        custom_objects={'all_loss':all_loss,
                                                                    'f2_m':f2_m})
    # if not 'zero_cell_model' in model_dict:
    #     model_dict['zero_cell_model'] = models.load_model(params['tracking']['zero_cell_model'],
    #                                     custom_objects={'absolute_dice_loss':absolute_dice_loss,
    #                                                                 'f2_m':f2_m})
    # if not 'one_cell_model' in model_dict:
    #     model_dict['one_cell_model'] = models.load_model(params['tracking']['one_cell_model'],
    #                                     custom_objects={'bce_dice_loss':bce_dice_loss,
    #                                                                 'f2_m':f2_m})
    # if not 'two_cell_model' in model_dict:
    #     model_dict['two_cell_model'] = models.load_model(params['tracking']['two_cell_model'],
    #                                     custom_objects={'all_loss':all_loss,
    #                                                                 'f2_m':f2_m})
    # if not 'geq_three_cell_model' in model_dict:
    #     model_dict['geq_three_cell_model'] = models.load_model(params['tracking']['geq_three_cell_model'],
    #                                     custom_objects={'bce_dice_loss':bce_dice_loss,
    #                                                                 'f2_m':f2_m})

    return(model_dict)

# Creates lineage for a single channel
def make_lineage_chnl_stack(fov_and_peak_id):
    '''
    Create the lineage for a set of segmented images for one channel. Start by making the regions in the first time points potenial cells. Go forward in time and map regions in the timepoint to the potential cells in previous time points, building the life of a cell. Used basic checks such as the regions should overlap, and grow by a little and not shrink too much. If regions do not link back in time, discard them. If two regions map to one previous region, check if it is a sensible division event.

    Parameters
    ----------
    fov_and_peak_ids : tuple.
        (fov_id, peak_id)

    Returns
    -------
    Cells : dict
        A dictionary of all the cells from this lineage, divided and undivided

    '''

    # load in parameters
    # if leaf regions see no action for longer than this, drop them
    lost_cell_time = params['track']['lost_cell_time']
    # only cells with y positions below this value will recieve the honor of becoming new
    # cells, unless they are daughters of current cells
    new_cell_y_cutoff = params['track']['new_cell_y_cutoff']
    # only regions with labels less than or equal to this value will be considered to start cells
    new_cell_region_cutoff = params['track']['new_cell_region_cutoff']

    # get the specific ids from the tuple
    fov_id, peak_id = fov_and_peak_id

    # start time is the first time point for this series of TIFFs.
    start_time_index = min(params['time_table'][fov_id].keys())

    information('Creating lineage for FOV %d, channel %d.' % (fov_id, peak_id))

    # load segmented data
    image_data_seg = load_stack(fov_id, peak_id, color=params['track']['seg_img'])
    # image_data_seg = load_stack(fov_id, peak_id, color='seg')

    # Calculate all data for all time points.
    # this list will be length of the number of time points
    regions_by_time = [regionprops(label_image=timepoint) for timepoint in image_data_seg] # removed coordinates='xy'

    # Set up data structures.
    Cells = {} # Dict that holds all the cell objects, divided and undivided
    cell_leaves = [] # cell ids of the current leaves of the growing lineage tree

    # go through regions by timepoint and build lineages
    # timepoints start with the index of the first image
    for t, regions in enumerate(regions_by_time, start=start_time_index):
        # if there are cell leaves who are still waiting to be linked, but
        # too much time has passed, remove them.
        for leaf_id in cell_leaves:
            if t - Cells[leaf_id].times[-1] > lost_cell_time:
                cell_leaves.remove(leaf_id)

        # make all the regions leaves if there are no current leaves
        if not cell_leaves:
            for region in regions:
                if region.centroid[0] < new_cell_y_cutoff and region.label <= new_cell_region_cutoff:
                    # Create cell and put in cell dictionary
                    cell_id = create_cell_id(region, t, peak_id, fov_id)
                    Cells[cell_id] = Cell(cell_id, region, t, parent_id=None)

                    # add thes id to list of current leaves
                    cell_leaves.append(cell_id)

        # Determine if the regions are children of current leaves
        else:
            ### create mapping between regions and leaves
            leaf_region_map = {}
            leaf_region_map = {leaf_id : [] for leaf_id in cell_leaves}

            # get the last y position of current leaves and create tuple with the id
            current_leaf_positions = [(leaf_id, Cells[leaf_id].centroids[-1][0]) for leaf_id in cell_leaves]

            # go through regions, they will come off in Y position order
            for r, region in enumerate(regions):
                # create tuple which is cell_id of closest leaf, distance
                current_closest = (None, float('inf'))

                # check this region against all positions of all current leaf regions,
                # find the closest one in y.
                for leaf in current_leaf_positions:
                    # calculate distance between region and leaf
                    y_dist_region_to_leaf = abs(region.centroid[0] - leaf[1])

                    # if the distance is closer than before, update
                    if y_dist_region_to_leaf < current_closest[1]:
                        current_closest = (leaf[0], y_dist_region_to_leaf)

                # update map with the closest region
                leaf_region_map[current_closest[0]].append((r, y_dist_region_to_leaf))

            # go through the current leaf regions.
            # limit by the closest two current regions if there are three regions to the leaf
            for leaf_id, region_links in six.iteritems(leaf_region_map):
                if len(region_links) > 2:
                    closest_two_regions = sorted(region_links, key=lambda x: x[1])[:2]
                    # but sort by region order so top region is first
                    closest_two_regions = sorted(closest_two_regions, key=lambda x: x[0])
                    # replace value in dictionary
                    leaf_region_map[leaf_id] = closest_two_regions

                    # for the discarded regions, put them as new leaves
                    # if they are near the closed end of the channel
                    discarded_regions = sorted(region_links, key=lambda x: x[1])[2:]
                    for discarded_region in discarded_regions:
                        region = regions[discarded_region[0]]
                        if region.centroid[0] < new_cell_y_cutoff and region.label <= new_cell_region_cutoff:
                            cell_id = create_cell_id(region, t, peak_id, fov_id)
                            Cells[cell_id] = Cell(cell_id, region, t, parent_id=None)
                            cell_leaves.append(cell_id) # add to leaves
                        else:
                            # since the regions are ordered, none of the remaining will pass
                            break

            ### iterate over the leaves, looking to see what regions connect to them.
            for leaf_id, region_links in six.iteritems(leaf_region_map):

                # if there is just one suggested descendant,
                # see if it checks out and append the data
                if len(region_links) == 1:
                    region = regions[region_links[0][0]] # grab the region from the list

                    # check if the pairing makes sense based on size and position
                    # this function returns true if things are okay
                    if check_growth_by_region(Cells[leaf_id], region):
                        # grow the cell by the region in this case
                        Cells[leaf_id].grow(region, t)

                # there may be two daughters, or maybe there is just one child and a new cell
                elif len(region_links) == 2:
                    # grab these two daughters
                    region1 = regions[region_links[0][0]]
                    region2 = regions[region_links[1][0]]

                    # check_division returns 3 if cell divided,
                    # 1 if first region is just the cell growing and the second is trash
                    # 2 if the second region is the cell, and the first is trash
                    # or 0 if it cannot be determined.
                    check_division_result = check_division(Cells[leaf_id], region1, region2)

                    if check_division_result == 3:
                        # create two new cells and divide the mother
                        daughter1_id = create_cell_id(region1, t, peak_id, fov_id)
                        daughter2_id = create_cell_id(region2, t, peak_id, fov_id)
                        Cells[daughter1_id] = Cell(daughter1_id, region1, t,
                                                   parent_id=leaf_id)
                        Cells[daughter2_id] = Cell(daughter2_id, region2, t,
                                                   parent_id=leaf_id)
                        Cells[leaf_id].divide(Cells[daughter1_id], Cells[daughter2_id], t)

                        # remove mother from current leaves
                        cell_leaves.remove(leaf_id)

                        # add the daughter ids to list of current leaves if they pass cutoffs
                        if region1.centroid[0] < new_cell_y_cutoff and region1.label <= new_cell_region_cutoff:
                            cell_leaves.append(daughter1_id)

                        if region2.centroid[0] < new_cell_y_cutoff and region2.label <= new_cell_region_cutoff:
                            cell_leaves.append(daughter2_id)

                    # 1 means that daughter 1 is just a continuation of the mother
                    # The other region should be a leaf it passes the requirements
                    elif check_division_result == 1:
                        Cells[leaf_id].grow(region1, t)

                        if region2.centroid[0] < new_cell_y_cutoff and region2.label <= new_cell_region_cutoff:
                            cell_id = create_cell_id(region2, t, peak_id, fov_id)
                            Cells[cell_id] = Cell(cell_id, region2, t, parent_id=None)
                            cell_leaves.append(cell_id) # add to leaves

                    # ditto for 2
                    elif check_division_result == 2:
                        Cells[leaf_id].grow(region2, t)

                        if region1.centroid[0] < new_cell_y_cutoff and region1.label <=     new_cell_region_cutoff:
                            cell_id = create_cell_id(region1, t, peak_id, fov_id)
                            Cells[cell_id] = Cell(cell_id, region1, t, parent_id=None)
                            cell_leaves.append(cell_id) # add to leaves

    # return the dictionary with all the cells
    return Cells

### Cell class and related functions

# this is the object that holds all information for a detection
class Detection():
    '''
    The Detection is a single detection in a single frame.
    '''

    # initialize (birth) the cell
    def __init__(self, detection_id, region, t):
        '''The detection must be given a unique detection_id and passed the region
        information from the segmentation

        Parameters
        __________

        detection_id : str
            detection_id is a string in the form fXpXtXrX
            f is 3 digit FOV number
            p is 4 digit peak number
            t is 4 digit time point
            r is region label for that segmentation
            Use the function create_detection_id to return a proper string.

        region : region properties object
            Information about the labeled region from
            skimage.measure.regionprops()

            '''

        # create all the attributes
        # id
        self.id = detection_id

        # identification convenience
        self.fov = int(detection_id.split('f')[1].split('p')[0])
        self.peak = int(detection_id.split('p')[1].split('t')[0])
        self.t = t

        self.cell_count = 1

        # self.abs_times = [params['time_table'][self.fov][t]] # elapsed time in seconds
        if region is not None:
            self.label = region.label
            self.bbox = region.bbox
            self.area = region.area

            # calculating cell length and width by using Feret Diamter. These values are in pixels
            length_tmp, width_tmp = feretdiameter(region)
            if length_tmp == None:
                warning('feretdiameter() failed for ' + self.id + ' at t=' + str(t) + '.')
            self.length = length_tmp
            self.width = width_tmp

            # calculate cell volume as cylinder plus hemispherical ends (sphere). Unit is px^3
            self.volume = (length_tmp - width_tmp) * np.pi * (width_tmp/2)**2 + (4/3) * np.pi * (width_tmp/2)**3

            # angle of the fit elipsoid and centroid location
            self.orientation = region.orientation
            self.centroid = region.centroid

        else:
            self.label = None
            self.bbox = None
            self.area = None

            # calculating cell length and width by using Feret Diamter. These values are in pixels
            length_tmp, width_tmp = (None, None)
            self.length = None
            self.width = None

            # calculate cell volume as cylinder plus hemispherical ends (sphere). Unit is px^3
            self.volume = None

            # angle of the fit elipsoid and centroid location
            self.orientation = None
            self.centroid = None

# this is the object that holds all information for a cell
class Cell():
    '''
    The Cell class is one cell that has been born. It is not neccesarily a cell that
    has divided.
    '''

    # initialize (birth) the cell
    def __init__(self, cell_id, region, t, parent_id=None):
        '''The cell must be given a unique cell_id and passed the region
        information from the segmentation

        Parameters
        __________

        cell_id : str
            cell_id is a string in the form fXpXtXrX
            f is 3 digit FOV number
            p is 4 digit peak number
            t is 4 digit time point at time of birth
            r is region label for that segmentation
            Use the function create_cell_id to do return a proper string.

        region : region properties object
            Information about the labeled region from
            skimage.measure.regionprops()

        parent_id : str
            id of the parent if there is one.
            '''

        # create all the attributes
        # id
        self.id = cell_id

        # identification convenience
        self.fov = int(cell_id.split('f')[1].split('p')[0])
        self.peak = int(cell_id.split('p')[1].split('t')[0])
        self.birth_label = int(cell_id.split('r')[1])

        # parent id may be none
        self.parent = parent_id

        # daughters is updated when cell divides
        # if this is none then the cell did not divide
        self.daughters = None

        # birth and division time
        self.birth_time = t
        self.division_time = None # filled out if cell divides

        # the following information is on a per timepoint basis
        self.times = [t]
        self.abs_times = [params['time_table'][self.fov][t]] # elapsed time in seconds
        self.labels = [region.label]
        self.bboxes = [region.bbox]
        self.areas = [region.area]

        # calculating cell length and width by using Feret Diamter. These values are in pixels
        length_tmp, width_tmp = feretdiameter(region)
        if length_tmp == None:
            warning('feretdiameter() failed for ' + self.id + ' at t=' + str(t) + '.')
        self.lengths = [length_tmp]
        self.widths = [width_tmp]

        # calculate cell volume as cylinder plus hemispherical ends (sphere). Unit is px^3
        self.volumes = [(length_tmp - width_tmp) * np.pi * (width_tmp/2)**2 +
                       (4/3) * np.pi * (width_tmp/2)**3]

        # angle of the fit elipsoid and centroid location
        self.orientations = [region.orientation]
        self.centroids = [region.centroid]

        # these are special datatype, as they include information from the daugthers for division
        # computed upon division
        self.times_w_div = None
        self.lengths_w_div = None
        self.widths_w_div = None

        # this information is the "production" information that
        # we want to extract at the end. Some of this is for convenience.
        # This is only filled out if a cell divides.
        self.sb = None # in um
        self.sd = None # this should be combined lengths of daughters, in um
        self.delta = None
        self.tau = None
        self.elong_rate = None
        self.septum_position = None
        self.width = None
        self.death = None

    def grow(self, region, t):
        '''Append data from a region to this cell.
        use cell.times[-1] to get most current value'''

        self.times.append(t)
        self.abs_times.append(params['time_table'][self.fov][t])
        self.labels.append(region.label)
        self.bboxes.append(region.bbox)
        self.areas.append(region.area)

        #calculating cell length and width by using Feret Diamter
        length_tmp, width_tmp = feretdiameter(region)
        if length_tmp == None:
            warning('feretdiameter() failed for ' + self.id + ' at t=' + str(t) + '.')
        self.lengths.append(length_tmp)
        self.widths.append(width_tmp)
        self.volumes.append((length_tmp - width_tmp) * np.pi * (width_tmp/2)**2 +
                            (4/3) * np.pi * (width_tmp/2)**3)

        self.orientations.append(region.orientation)
        self.centroids.append(region.centroid)

    def die(self, region, t):
        '''
        Annotate cell as dying from current t to next t.
        '''
        self.death = t

    def divide(self, daughter1, daughter2, t):
        '''Divide the cell and update stats.
        daugther1 and daugther2 are instances of the Cell class.
        daughter1 is the daugther closer to the closed end.'''

        # put the daugther ids into the cell
        self.daughters = [daughter1.id, daughter2.id]

        # give this guy a division time
        self.division_time = daughter1.birth_time

        # update times
        self.times_w_div = self.times + [self.division_time]
        self.abs_times.append(params['time_table'][self.fov][self.division_time])

        # flesh out the stats for this cell
        # size at birth
        self.sb = self.lengths[0] * params['pxl2um']

        # force the division length to be the combined lengths of the daughters
        self.sd = (daughter1.lengths[0] + daughter2.lengths[0]) * params['pxl2um']

        # delta is here for convenience
        self.delta = self.sd - self.sb

        # generation time. Use more accurate times and convert to minutes
        self.tau = np.float64((self.abs_times[-1] - self.abs_times[0]) / 60.0)

        # include the data points from the daughters
        self.lengths_w_div = [l * params['pxl2um'] for l in self.lengths] + [self.sd]
        self.widths_w_div = [w * params['pxl2um'] for w in self.widths] + [((daughter1.widths[0] + daughter2.widths[0])/2) * params['pxl2um']]

        # volumes for all timepoints, in um^3
        self.volumes_w_div = []
        for i in range(len(self.lengths_w_div)):
            self.volumes_w_div.append((self.lengths_w_div[i] - self.widths_w_div[i]) *
                                       np.pi * (self.widths_w_div[i]/2)**2 +
                                       (4/3) * np.pi * (self.widths_w_div[i]/2)**3)

        # calculate elongation rate.

        try:
            times = np.float64((np.array(self.abs_times) - self.abs_times[0]) / 60.0)
            log_lengths = np.float64(np.log(self.lengths_w_div))
            p = np.polyfit(times, log_lengths, 1) # this wants float64
            self.elong_rate = p[0] * 60.0 # convert to hours

        except:
            self.elong_rate = np.float64('NaN')
            warning('Elongation rate calculate failed for {}.'.format(self.id))

        # calculate the septum position as a number between 0 and 1
        # which indicates the size of daughter closer to the closed end
        # compared to the total size
        self.septum_position = daughter1.lengths[0] / (daughter1.lengths[0] + daughter2.lengths[0])

        # calculate single width over cell's life
        self.width = np.mean(self.widths_w_div)

        # convert data to smaller floats. No need for float64
        # see https://docs.scipy.org/doc/numpy-1.13.0/user/basics.types.html
        convert_to = 'float16' # numpy datatype to convert to

        self.sb = self.sb.astype(convert_to)
        self.sd = self.sd.astype(convert_to)
        self.delta = self.delta.astype(convert_to)
        self.elong_rate = self.elong_rate.astype(convert_to)
        self.tau = self.tau.astype(convert_to)
        self.septum_position = self.septum_position.astype(convert_to)
        self.width = self.width.astype(convert_to)

        self.lengths = [length.astype(convert_to) for length in self.lengths]
        self.lengths_w_div = [length.astype(convert_to) for length in self.lengths_w_div]
        self.widths = [width.astype(convert_to) for width in self.widths]
        self.widths_w_div = [width.astype(convert_to) for width in self.widths_w_div]
        self.volumes = [vol.astype(convert_to) for vol in self.volumes]
        self.volumes_w_div = [vol.astype(convert_to) for vol in self.volumes_w_div]
        # note the float16 is hardcoded here
        self.orientations = [np.float16(orientation) for orientation in self.orientations]
        self.centroids = [(y.astype(convert_to), x.astype(convert_to)) for y, x in self.centroids]

    def print_info(self):
        '''prints information about the cell'''
        print('id = %s' % self.id)
        print('times = {}'.format(', '.join('{}'.format(t) for t in self.times)))
        print('lengths = {}'.format(', '.join('{:.2f}'.format(l) for l in self.lengths)))

class CellTree():

    def __init__(self):
        self.cells = {}
        self.scores = [] # probably needs to be different
        self.score = 0
        self.cell_id_list = []

    def add_cell(self, cell):
        self.cells[cell.id] = cell
        self.cell_id_list.append(cell.id)
        self.cell_id_list.sort()

    def update_score(self):
        pass

    def get_cell(self, cell_id):
        return(self.cells[cell_id])

    def get_top_from_cell(self, cell_id):
        pass

# this is the object that holds all information for a cell
class CellFromGraph():
    '''
    The CellFromGraph class is one cell that has been born.
    It is not neccesarily a cell that has divided.
    '''

    # initialize (birth) the cell
    def __init__(self, cell_id, region, t, parent=None):
        '''The cell must be given a unique cell_id and passed the region
        information from the segmentation

        Parameters
        __________

        cell_id : str
            cell_id is a string in the form fXpXtXrX
            f is 3 digit FOV number
            p is 4 digit peak number
            t is 4 digit time point at time of birth
            r is region label for that segmentation
            Use the function create_cell_id to do return a proper string.

        region : region properties object
            Information about the labeled region from
            skimage.measure.regionprops()

        parent_id : str
            id of the parent if there is one.
            '''

        # create all the attributes
        # id
        self.id = cell_id

        # identification convenience
        self.fov = int(cell_id.split('f')[1].split('p')[0])
        self.peak = int(cell_id.split('p')[1].split('t')[0])
        self.birth_label = int(region.label)
        self.regions = [region]

        # parent is a CellFromGraph object, can be None
        self.parent = parent

        # daughters is updated when cell divides
        # if this is none then the cell did not divide
        self.daughters = None

        # birth and division time
        self.birth_time = t
        self.division_time = None # filled out if cell divides

        # the following information is on a per timepoint basis
        self.times = [t]
        self.abs_times = [params['time_table'][self.fov][t]] # elapsed time in seconds
        self.labels = [region.label]
        self.bboxes = [region.bbox]
        self.areas = [region.area]

        # calculating cell length and width by using Feret Diamter. These values are in pixels
        length_tmp, width_tmp = feretdiameter(region)
        if length_tmp == None:
            warning('feretdiameter() failed for ' + self.id + ' at t=' + str(t) + '.')
        self.lengths = [length_tmp]
        self.widths = [width_tmp]

        # calculate cell volume as cylinder plus hemispherical ends (sphere). Unit is px^3
        self.volumes = [(length_tmp - width_tmp) * np.pi * (width_tmp/2)**2 +
                       (4/3) * np.pi * (width_tmp/2)**3]

        # angle of the fit elipsoid and centroid location
        self.orientations = [region.orientation]
        self.centroids = [region.centroid]

        # these are special datatype, as they include information from the daugthers for division
        # computed upon division
        self.times_w_div = None
        self.lengths_w_div = None
        self.widths_w_div = None

        # this information is the "production" information that
        # we want to extract at the end. Some of this is for convenience.
        # This is only filled out if a cell divides.
        self.sb = None # in um
        self.sd = None # this should be combined lengths of daughters, in um
        self.delta = None
        self.tau = None
        self.elong_rate = None
        self.septum_position = None
        self.width = None
        self.death = None
        self.disappear = None
        self.area_mean_fluorescence = {}
        self.volume_mean_fluorescence = {}
        self.total_fluorescence = {}
        self.foci = {}

    def __len__(self):
        return(len(self.times))

    def add_parent(self, parent):
        self.parent = parent

    def grow(self, region, t):
        '''Append data from a region to this cell.
        use cell.times[-1] to get most current value'''

        self.times.append(t)
        self.abs_times.append(params['time_table'][self.fov][t])
        self.labels.append(region.label)
        self.bboxes.append(region.bbox)
        self.areas.append(region.area)
        self.regions.append(region)

        #calculating cell length and width by using Feret Diamter
        length_tmp, width_tmp = feretdiameter(region)
        if length_tmp == None:
            warning('feretdiameter() failed for ' + self.id + ' at t=' + str(t) + '.')
        self.lengths.append(length_tmp)
        self.widths.append(width_tmp)
        self.volumes.append((length_tmp - width_tmp) * np.pi * (width_tmp/2)**2 +
                            (4/3) * np.pi * (width_tmp/2)**3)

        self.orientations.append(region.orientation)
        self.centroids.append(region.centroid)

    def die(self, region, t):
        '''
        Annotate cell as dying from current t to next t.
        '''
        self.death = t

    def disappears(self, region, t):
        '''
        Annotate cell as disappearing from current t to next t.
        '''
        self.disappear = t

    def add_daughter(self, daughter, t):

        if self.daughters is None:
            self.daughters = [daughter]
        else:
            self.daughters.append(daughter)
            assert len(self.daughters) < 3, "Too many daughter cells in cell {}".format(self.id)
            # sort daughters by y position, with smaller y-value first.
            # this will cause the daughter closer to the closed end of the trap to be listed first.
            self.daughters.sort(key=lambda cell: cell.centroids[0][0])
            self.divide(t)

    def divide(self, t):
        '''Divide the cell and update stats.
        daughter1 is the daugther closer to the closed end.'''

        # put the daugther ids into the cell
        # self.daughters = [daughter1.id, daughter2.id]

        # give this guy a division time
        self.division_time = self.daughters[0].birth_time

        # update times
        self.times_w_div = self.times + [self.division_time]
        self.abs_times.append(params['time_table'][self.fov][self.division_time])

        # flesh out the stats for this cell
        # size at birth
        self.sb = self.lengths[0] * params['pxl2um']

        # force the division length to be the combined lengths of the daughters
        self.sd = (self.daughters[0].lengths[0] + self.daughters[1].lengths[0]) * params['pxl2um']

        # delta is here for convenience
        self.delta = self.sd - self.sb

        # generation time. Use more accurate times and convert to minutes
        self.tau = np.float64((self.abs_times[-1] - self.abs_times[0]) / 60.0)

        # include the data points from the daughters
        self.lengths_w_div = [l * params['pxl2um'] for l in self.lengths] + [self.sd]
        self.widths_w_div = [w * params['pxl2um'] for w in self.widths] + [((self.daughters[0].widths[0] + self.daughters[1].widths[0])/2) * params['pxl2um']]

        # volumes for all timepoints, in um^3
        self.volumes_w_div = []
        for i in range(len(self.lengths_w_div)):
            self.volumes_w_div.append((self.lengths_w_div[i] - self.widths_w_div[i]) *
                                       np.pi * (self.widths_w_div[i]/2)**2 +
                                       (4/3) * np.pi * (self.widths_w_div[i]/2)**3)

        # calculate elongation rate.
        try:
            times = np.float64((np.array(self.abs_times) - self.abs_times[0]) / 60.0) # convert times to minutes
            log_lengths = np.float64(np.log(self.lengths_w_div))
            p = np.polyfit(times, log_lengths, 1) # this wants float64
            self.elong_rate = p[0] * 60.0 # convert to hours
        except:
            self.elong_rate = np.float64('NaN')
            warning('Elongation rate calculate failed for {}.'.format(self.id))

        # calculate the septum position as a number between 0 and 1
        # which indicates the size of daughter closer to the closed end
        # compared to the total size
        self.septum_position = self.daughters[0].lengths[0] / (self.daughters[0].lengths[0] + self.daughters[1].lengths[0])

        # calculate single width over cell's life
        self.width = np.mean(self.widths_w_div)

        # convert data to smaller floats. No need for float64
        # see https://docs.scipy.org/doc/numpy-1.13.0/user/basics.types.html
        convert_to = 'float16' # numpy datatype to convert to

        self.sb = self.sb.astype(convert_to)
        self.sd = self.sd.astype(convert_to)
        self.delta = self.delta.astype(convert_to)
        self.elong_rate = self.elong_rate.astype(convert_to)
        self.tau = self.tau.astype(convert_to)
        self.septum_position = self.septum_position.astype(convert_to)
        self.width = self.width.astype(convert_to)

        self.lengths = [length.astype(convert_to) for length in self.lengths]
        self.lengths_w_div = [length.astype(convert_to) for length in self.lengths_w_div]
        self.widths = [width.astype(convert_to) for width in self.widths]
        self.widths_w_div = [width.astype(convert_to) for width in self.widths_w_div]
        self.volumes = [vol.astype(convert_to) for vol in self.volumes]
        self.volumes_w_div = [vol.astype(convert_to) for vol in self.volumes_w_div]
        # note the float16 is hardcoded here
        self.orientations = [np.float16(orientation) for orientation in self.orientations]
        self.centroids = [(y.astype(convert_to), x.astype(convert_to)) for y, x in self.centroids]

    def add_focus(self, focus, t):
        '''Adds a focus to the cell. See function foci_info_unet'''
        self.foci[focus.id] = focus

    def print_info(self):
        '''prints information about the cell'''
        print('id = %s' % self.id)
        print('times = {}'.format(', '.join('{}'.format(t) for t in self.times)))
        print('lengths = {}'.format(', '.join('{:.2f}'.format(l) for l in self.lengths)))
        if self.daughters is not None:
            print('daughters = {}'.format(', '.join('{}'.format(daughter.id) for daughter in self.daughters)))
        if self.parent is not None:
            print('parent = {}'.format(self.parent.id))

    def make_wide_df(self):

        data = {}
        data['id'] = self.id
        data['fov'] = self.fov
        data['trap'] = self.peak
        data['parent'] = self.parent
        data['child1'] = None
        data['child2'] = None
        data['division_time'] = self.division_time
        data['birth_label'] = self.birth_label
        data['birth_time'] = self.birth_time
        data['sb'] = self.sb
        data['sd'] = self.sd
        data['delta'] = self.delta
        data['tau'] = self.tau
        data['elong_rate'] = self.elong_rate
        data['septum_position'] = self.septum_position
        data['death'] = self.death
        data['disappear'] = self.disappear

        if self.daughters is not None:
            data['child1'] = self.daughters[0]

            if len(self.daughters) == 2:
                data['child2'] = self.daughters[1]

        df = pd.DataFrame(data, index=[self.id])
        return(df)

    def make_long_df(self):

        data = {}
        data['id'] = [self.id]*len(self.times)
        data['times'] = self.times
        data['length'] = self.lengths
        data['volume'] = self.volumes
        data['area'] = self.areas

        # if a cell divides then there is one extra value in abs_times
        if self.division_time is None:
            data['seconds'] = self.abs_times
        else:
            data['seconds'] = self.abs_times[:-1]

        # if there is fluorescence data, place it into the dataframe
        if len(self.area_mean_fluorescence.keys()) != 0:

            for fluorescence_channel in self.area_mean_fluorescence.keys():

                data['{}_area_mean_fluorescence'.format(fluorescence_channel)] = self.area_mean_fluorescence[fluorescence_channel]
                data['{}_volume_mean_fluorescence'.format(fluorescence_channel)] = self.volume_mean_fluorescence[fluorescence_channel]
                data['{}_total_fluorescence'.format(fluorescence_channel)] = self.total_fluorescence[fluorescence_channel]

        df = pd.DataFrame(data, index=data['id'])

        return(df)


# obtains cell length and width of the cell using the feret diameter
def feretdiameter(region):
    '''
    feretdiameter calculates the length and width of the binary region shape. The cell orientation
    from the ellipsoid is used to find the major and minor axis of the cell.
    See https://en.wikipedia.org/wiki/Feret_diameter.
    '''

    # y: along vertical axis of the image; x: along horizontal axis of the image;
    # calculate the relative centroid in the bounding box (non-rotated)
    # print(region.centroid)
    y0, x0 = region.centroid
    y0 = y0 - np.int16(region.bbox[0]) + 1
    x0 = x0 - np.int16(region.bbox[1]) + 1

    ## orientation is now measured in RC coordinates - quick fix to convert
    ## back to xy
    if region.orientation > 0:
        ori1 = np.pi / 2 - region.orientation
    else:
        ori1 = - np.pi / 2 - region.orientation
    cosorient = np.cos(ori1)
    sinorient = np.sin(ori1)

    amp_param = 1.2 #amplifying number to make sure the axis is longer than actual cell length

    # coordinates relative to bounding box
    # r_coords = region.coords - [np.int16(region.bbox[0]), np.int16(region.bbox[1])]

    # limit to perimeter coords. pixels are relative to bounding box
    region_binimg = np.pad(region.image, 1, 'constant') # pad region binary image by 1 to avoid boundary non-zero pixels
    distance_image = ndi.distance_transform_edt(region_binimg)
    r_coords = np.where(distance_image == 1)
    r_coords = list(zip(r_coords[0], r_coords[1]))

    # coordinates are already sorted by y. partion into top and bottom to search faster later
    # if orientation > 0, L1 is closer to top of image (lower Y coord)
    if (ori1) > 0:
        L1_coords = r_coords[:int(np.round(len(r_coords)/4))]
        L2_coords = r_coords[int(np.round(len(r_coords)/4)):]
    else:
        L1_coords = r_coords[int(np.round(len(r_coords)/4)):]
        L2_coords = r_coords[:int(np.round(len(r_coords)/4))]

    #####################
    # calculte cell length
    L1_pt = np.zeros((2,1))
    L2_pt = np.zeros((2,1))

    # define the two end points of the the long axis line
    # one pole.
    L1_pt[1] = x0 + cosorient * 0.5 * region.major_axis_length*amp_param
    L1_pt[0] = y0 - sinorient * 0.5 * region.major_axis_length*amp_param

    # the other pole.
    L2_pt[1] = x0 - cosorient * 0.5 * region.major_axis_length*amp_param
    L2_pt[0] = y0 + sinorient * 0.5 * region.major_axis_length*amp_param


    # calculate the minimal distance between the points at both ends of 3 lines
    # aka calcule the closest coordiante in the region to each of the above points.
    # pt_L1 = r_coords[np.argmin([np.sqrt(np.power(Pt[0]-L1_pt[0],2) + np.power(Pt[1]-L1_pt[1],2)) for Pt in r_coords])]
    # pt_L2 = r_coords[np.argmin([np.sqrt(np.power(Pt[0]-L2_pt[0],2) + np.power(Pt[1]-L2_pt[1],2)) for Pt in r_coords])]

    try:
        pt_L1 = L1_coords[np.argmin([np.sqrt(np.power(Pt[0]-L1_pt[0],2) + np.power(Pt[1]-L1_pt[1],2)) for Pt in L1_coords])]
        pt_L2 = L2_coords[np.argmin([np.sqrt(np.power(Pt[0]-L2_pt[0],2) + np.power(Pt[1]-L2_pt[1],2)) for Pt in L2_coords])]
        length = np.sqrt(np.power(pt_L1[0]-pt_L2[0],2) + np.power(pt_L1[1]-pt_L2[1],2))
    except:
        length = None

    #####################
    # calculate cell width
    # draw 2 parallel lines along the short axis line spaced by 0.8*quarter of length = 0.4, to avoid  in midcell

    # limit to points in each half
    W_coords = []
    if (ori1) > 0:
        W_coords.append(r_coords[:int(np.round(len(r_coords)/2))]) # note the /2 here instead of /4
        W_coords.append(r_coords[int(np.round(len(r_coords)/2)):])
    else:
        W_coords.append(r_coords[int(np.round(len(r_coords)/2)):])
        W_coords.append(r_coords[:int(np.round(len(r_coords)/2))])

    # starting points
    x1 = x0 + cosorient * 0.5 * length*0.4
    y1 = y0 - sinorient * 0.5 * length*0.4
    x2 = x0 - cosorient * 0.5 * length*0.4
    y2 = y0 + sinorient * 0.5 * length*0.4
    W1_pts = np.zeros((2,2))
    W2_pts = np.zeros((2,2))

    # now find the ends of the lines
    # one side
    W1_pts[0,1] = x1 - sinorient * 0.5 * region.minor_axis_length*amp_param
    W1_pts[0,0] = y1 - cosorient * 0.5 * region.minor_axis_length*amp_param
    W1_pts[1,1] = x2 - sinorient * 0.5 * region.minor_axis_length*amp_param
    W1_pts[1,0] = y2 - cosorient * 0.5 * region.minor_axis_length*amp_param

    # the other side
    W2_pts[0,1] = x1 + sinorient * 0.5 * region.minor_axis_length*amp_param
    W2_pts[0,0] = y1 + cosorient * 0.5 * region.minor_axis_length*amp_param
    W2_pts[1,1] = x2 + sinorient * 0.5 * region.minor_axis_length*amp_param
    W2_pts[1,0] = y2 + cosorient * 0.5 * region.minor_axis_length*amp_param

    # calculate the minimal distance between the points at both ends of 3 lines
    pt_W1 = np.zeros((2,2))
    pt_W2 = np.zeros((2,2))
    d_W = np.zeros((2,1))
    i = 0
    for W1_pt, W2_pt in zip(W1_pts, W2_pts):

        # # find the points closest to the guide points
        # pt_W1[i,0], pt_W1[i,1] = r_coords[np.argmin([np.sqrt(np.power(Pt[0]-W1_pt[0],2) + np.power(Pt[1]-W1_pt[1],2)) for Pt in r_coords])]
        # pt_W2[i,0], pt_W2[i,1] = r_coords[np.argmin([np.sqrt(np.power(Pt[0]-W2_pt[0],2) + np.power(Pt[1]-W2_pt[1],2)) for Pt in r_coords])]

        # find the points closest to the guide points
        pt_W1[i,0], pt_W1[i,1] = W_coords[i][np.argmin([np.sqrt(np.power(Pt[0]-W1_pt[0],2) + np.power(Pt[1]-W1_pt[1],2)) for Pt in W_coords[i]])]
        pt_W2[i,0], pt_W2[i,1] = W_coords[i][np.argmin([np.sqrt(np.power(Pt[0]-W2_pt[0],2) + np.power(Pt[1]-W2_pt[1],2)) for Pt in W_coords[i]])]

        # calculate the actual width
        d_W[i] = np.sqrt(np.power(pt_W1[i,0]-pt_W2[i,0],2) + np.power(pt_W1[i,1]-pt_W2[i,1],2))
        i += 1

    # take the average of the two at quarter positions
    width = np.mean([d_W[0],d_W[1]])
    return length, width

# take info and make string for cell id
def create_focus_id(region, t, peak, fov, experiment_name=None):
    '''Make a unique focus id string for a new focus'''
    if experiment_name is None:
        focus_id = 'f{:0=2}p{:0=4}t{:0=4}r{:0=2}'.format(fov, peak, t, region.label)
    else:
        focus_id = '{}f{:0=2}p{:0=4}t{:0=4}r{:0=2}'.format(experiment_name, fov, peak, t, region.label)
    return focus_id

# take info and make string for cell id
def create_cell_id(region, t, peak, fov, experiment_name=None):
    '''Make a unique cell id string for a new cell'''
    # cell_id = ['f', str(fov), 'p', str(peak), 't', str(t), 'r', str(region.label)]
    if experiment_name is None:
        cell_id = ['f', '%02d' % fov, 'p', '%04d' % peak, 't', '%04d' % t, 'r', '%02d' % region.label]
        cell_id = ''.join(cell_id)
    else:
        cell_id = '{}f{:0=2}p{:0=4}t{:0=4}r{:0=2}'.format(experiment_name, fov, peak, t, region.label)
    return cell_id

# function for a growing cell, used to calculate growth rate
def cell_growth_func(t, sb, elong_rate):
    '''
    Assumes you have taken log of the data.
    It also allows the size at birth to be a free parameter, rather than fixed
    at the actual size at birth (but still uses that as a guess)
    Assumes natural log, not base 2 (though I think that makes less sense)

    old form: sb*2**(alpha*t)
    '''
    return sb+elong_rate*t

# functions for checking if a cell has divided or not
# this function should also take the variable t to
# weight the allowed changes by the difference in time as well
def check_growth_by_region(cell, region):
    '''Checks to see if it makes sense
    to grow a cell by a particular region'''
    # load parameters for checking
    max_growth_length = params['track']['max_growth_length']
    min_growth_length = params['track']['min_growth_length']
    max_growth_area = params['track']['max_growth_area']
    min_growth_area = params['track']['min_growth_area']

    # check if length is not too much longer
    if cell.lengths[-1]*max_growth_length < region.major_axis_length:
        return False

    # check if it is not too short (cell should not shrink really)
    if cell.lengths[-1]*min_growth_length > region.major_axis_length:
        return False

    # check if area is not too great
    if cell.areas[-1]*max_growth_area < region.area:
        return False

    # check if area is not too small
    if cell.lengths[-1]*min_growth_area > region.area:
        return False

    # # check if y position of region is within
    # # the quarter positions of the bounding box
    # lower_quarter = cell.bboxes[-1][0] + (region.major_axis_length / 4)
    # upper_quarter = cell.bboxes[-1][2] - (region.major_axis_length / 4)
    # if lower_quarter > region.centroid[0] or upper_quarter < region.centroid[0]:
    #     return False

    # check if y position of region is within the bounding box of previous region
    lower_bound = cell.bboxes[-1][0]
    upper_bound = cell.bboxes[-1][2]
    if lower_bound > region.centroid[0] or upper_bound < region.centroid[0]:
        return False

    # return true if you get this far
    return True

# see if a cell has reasonably divided
def check_division(cell, region1, region2):
    '''Checks to see if it makes sense to divide a
    cell into two new cells based on two regions.

    Return 0 if nothing should happend and regions ignored
    Return 1 if cell should grow by region 1
    Return 2 if cell should grow by region 2
    Return 3 if cell should divide into the regions.'''

    # load in parameters
    max_growth_length = params['track']['max_growth_length']
    min_growth_length = params['track']['min_growth_length']

    # see if either region just could be continued growth,
    # if that is the case then just return
    # these shouldn't return true if the cells are divided
    # as they would be too small
    if check_growth_by_region(cell, region1):
        return 1

    if check_growth_by_region(cell, region2):
        return 2

    # make sure combined size of daughters is not too big
    combined_size = region1.major_axis_length + region2.major_axis_length
    # check if length is not too much longer
    if cell.lengths[-1]*max_growth_length < combined_size:
        return 0
    # and not too small
    if cell.lengths[-1]*min_growth_length > combined_size:
        return 0

    # centroids of regions should be in the upper and lower half of the
    # of the mother's bounding box, respectively
    # top region within top half of mother bounding box
    if cell.bboxes[-1][0] > region1.centroid[0] or cell.centroids[-1][0] < region1.centroid[0]:
        return 0
    # bottom region with bottom half of mother bounding box
    if cell.centroids[-1][0] > region2.centroid[0] or cell.bboxes[-1][2] < region2.centroid[0]:
        return 0

    # if you got this far then divide the mother
    return 3

### functions for pruning a dictionary of cells
# find cells with both a mother and two daughters
def find_complete_cells(Cells):
    '''Go through a dictionary of cells and return another dictionary
    that contains just those with a parent and daughters'''

    Complete_Cells = {}

    for cell_id in Cells:
        if Cells[cell_id].daughters and Cells[cell_id].parent:
            Complete_Cells[cell_id] = Cells[cell_id]

    return Complete_Cells

# finds cells whose birth label is 1
def find_mother_cells(Cells):
    '''Return only cells whose starting region label is 1.'''

    Mother_Cells = {}

    for cell_id in Cells:
        if Cells[cell_id].birth_label == 1:
            Mother_Cells[cell_id] = Cells[cell_id]

    return Mother_Cells

def filter_cells(Cells, attr, val, idx=None, debug=False):
    '''Return only cells whose designated attribute equals "val".'''

    Filtered_Cells = {}

    for cell_id, cell in Cells.items():

        at_val = getattr(cell, attr)
        if debug:
            print(at_val)
            print("Times: ", cell.times)
        if idx is not None:
            at_val = at_val[idx]
        if at_val == val:
            Filtered_Cells[cell_id] = cell

    return Filtered_Cells

def filter_cells_containing_val_in_attr(Cells, attr, val):
    '''Return only cells that have val in list attribute, attr.'''

    Filtered_Cells = {}

    for cell_id, cell in Cells.items():

        at_list = getattr(cell, attr)
        if val in at_list:
            Filtered_Cells[cell_id] = cell

    return Filtered_Cells

### functions for additional cell centric analysis
def compile_cell_info_df(Cells):

    # count the number of rows that will be in the long dataframe
    quant_fluor = False
    long_df_row_number = 0
    for cell in Cells.values():

        # first time through, evaluate whether we quantified cells' fluorescence
        if long_df_row_number == 0:
            if len(cell.area_mean_fluorescence.keys()) != 0:
                quant_fluor = True
                fluorescence_channels = [k for k in cell.area_mean_fluorescence.keys()]

        long_df_row_number += len(cell.times)

    # initialize some arrays for filling with data
    data = {
        # ids can be up to 100 characters long
        'id': np.chararray(long_df_row_number, itemsize=100),
        'times': np.zeros(long_df_row_number, dtype='uint16'),
        'lengths': np.zeros(long_df_row_number),
        'volumes': np.zeros(long_df_row_number),
        'areas': np.zeros(long_df_row_number),
        'abs_times': np.zeros(long_df_row_number, dtype='uint32')
    }

    if quant_fluor:
        for fluorescence_channel in fluorescence_channels:
            data['{}_area_mean_fluorescence'.format(fluorescence_channel)] = np.zeros(long_df_row_number)
            data['{}_volume_mean_fluorescence'.format(fluorescence_channel)] = np.zeros(long_df_row_number)
            data['{}_total_fluorescence'.format(fluorescence_channel)] = np.zeros(long_df_row_number)

    data = populate_focus_arrays(Cells, data, cell_quants=True)
    long_df = pd.DataFrame(data=data)

    wide_df_row_number = len(Cells)
    data = {
        # ids can be up to 100 characters long
        'id': np.chararray(wide_df_row_number, itemsize=100),
        'fov': np.zeros(wide_df_row_number, dtype='uint8'),
        'peak': np.zeros(wide_df_row_number, dtype='uint16'),
        'parent_id': np.chararray(wide_df_row_number, itemsize=100),
        'child1_id': np.chararray(wide_df_row_number, itemsize=100),
        'child2_id': np.chararray(wide_df_row_number, itemsize=100),
        'division_time': np.zeros(wide_df_row_number),
        'birth_label': np.zeros(wide_df_row_number, dtype='uint8'),
        'birth_time': np.zeros(wide_df_row_number, dtype='uint16'),
        'sb': np.zeros(wide_df_row_number),
        'sd': np.zeros(wide_df_row_number),
        'delta': np.zeros(wide_df_row_number),
        'tau': np.zeros(wide_df_row_number),
        'elong_rate': np.zeros(wide_df_row_number),
        'septum_position': np.zeros(wide_df_row_number),
        'death': np.zeros(wide_df_row_number),
        'disappear': np.zeros(wide_df_row_number)
    }
    data = populate_focus_arrays(Cells, data, cell_quants=True, wide=True)
    # data['parent_id'] = data['parent_id'].decode()
    # data['child1_id'] = data['child1_id'].decode()
    # data['child2_id'] = data['child2_id'].decode()
    wide_df = pd.DataFrame(data=data)

    return(wide_df,long_df)

def find_all_cell_intensities(Cells,
                              specs, time_table, channel_name='sub_c2',
                              apply_background_correction=True):
    '''
    Finds fluorescenct information for cells. All the cells in Cells
    should be from one fov/peak.
    '''

    # iterate over each fov in specs
    for fov_id,fov_peaks in specs.items():

        # iterate over each peak in fov
        for peak_id,peak_value in fov_peaks.items():

            # if peak_id's value is not 1, go to next peak
            if peak_value != 1:
                continue

            print("Quantifying channel {} fluorescence in cells in fov {}, peak {}.".format(channel_name, fov_id, peak_id))
            # Load fluorescent images and segmented images for this channel
            fl_stack = load_stack(fov_id, peak_id, color=channel_name)
            corrected_stack = np.zeros(fl_stack.shape)

            for frame in range(fl_stack.shape[0]):
                # median filter will be applied to every image
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    median_filtered = median(fl_stack[frame,...], selem=morphology.disk(1))

                # subtract the gaussian-filtered image from true image to correct
                #   uneven background fluorescence
                if apply_background_correction:
                    blurred = filters.gaussian(median_filtered, sigma=10, preserve_range=True)
                    corrected_stack[frame,:,:] = median_filtered-blurred
                else:
                    corrected_stack[frame,:,:] = median_filtered

            seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

            # evaluate whether each cell is in this fov/peak combination
            for cell_id,cell in Cells.items():

                cell_fov = cell.fov
                if cell_fov != fov_id:
                    continue

                cell_peak = cell.peak
                if cell_peak != peak_id:
                    continue

                cell_times = cell.times
                cell_labels = cell.labels
                cell.area_mean_fluorescence[channel_name] = []
                cell.volume_mean_fluorescence[channel_name] = []
                cell.total_fluorescence[channel_name] = []

                # loop through cell's times
                for i,t in enumerate(cell_times):
                    frame = t-1
                    cell_label = cell_labels[i]

                    total_fluor = np.sum(corrected_stack[frame, seg_stack[frame, :,:] == cell_label])

                    cell.area_mean_fluorescence[channel_name].append(total_fluor/cell.areas[i])
                    cell.volume_mean_fluorescence[channel_name].append(total_fluor/cell.volumes[i])
                    cell.total_fluorescence[channel_name].append(total_fluor)

    # The cell objects in the original dictionary will be updated,
    # no need to return anything specifically.
    return

def find_cell_intensities_worker(fov_id, peak_id, Cells, midline=True, channel='sub_c3'):
    '''
    Finds fluorescenct information for cells. All the cells in Cells
    should be from one fov/peak. See the function
    organize_cells_by_channel()
    This version is the same as find_cell_intensities but return the Cells object for collection by the pool.
    The original find_cell_intensities is kept for compatibility.
    '''
    information('Processing peak {} in FOV {}'.format(peak_id, fov_id))
    # Load fluorescent images and segmented images for this channel
    fl_stack = load_stack(fov_id, peak_id, color=channel)
    seg_stack = load_stack(fov_id, peak_id, color='seg_otsu')

    # determine absolute time index
    time_table = params['time_table']
    times_all = []
    for fov in params['time_table']:
        times_all = np.append(times_all, [int(x) for x in time_table[fov].keys()])
    times_all = np.unique(times_all)
    times_all = np.sort(times_all)
    times_all = np.array(times_all,np.int_)
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():
        # give this cell two lists to hold new information
        Cell.fl_tots = [] # total fluorescence per time point
        Cell.fl_area_avgs = [] # avg fluorescence per unit area by timepoint
        Cell.fl_vol_avgs = [] # avg fluorescence per unit volume by timepoint

        if midline:
            Cell.mid_fl = [] # avg fluorescence of midline

        # and the time points that make up this cell's life
        for n, t in enumerate(Cell.times):
            # create fluorescent image only for this cell and timepoint.
            fl_image_masked = np.copy(fl_stack[t-t0])
            fl_image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # append total flourescent image
            Cell.fl_tots.append(np.sum(fl_image_masked))
            # and the average fluorescence
            Cell.fl_area_avgs.append(np.sum(fl_image_masked) / Cell.areas[n])
            Cell.fl_vol_avgs.append(np.sum(fl_image_masked) / Cell.volumes[n])

            if midline:
                # add the midline average by first applying morphology transform
                bin_mask = np.copy(seg_stack[t-t0])
                bin_mask[bin_mask != Cell.labels[n]] = 0
                med_mask, _ = morphology.medial_axis(bin_mask, return_distance=True)
                # med_mask[med_dist < np.floor(cap_radius/2)] = 0
                # print(img_fluo[med_mask])
                if (np.shape(fl_image_masked[med_mask])[0] > 0):
                    Cell.mid_fl.append(np.nanmean(fl_image_masked[med_mask]))
                else:
                    Cell.mid_fl.append(0)

    # return the cell object to the pool initiated by mm3_Colors.
    return Cells

def find_cell_intensities(fov_id, peak_id, Cells, midline=False, channel_name='sub_c2'):
    '''
    Finds fluorescenct information for cells. All the cells in Cells
    should be from one fov/peak. See the function
    organize_cells_by_channel()
    '''

    # Load fluorescent images and segmented images for this channel
    fl_stack = load_stack(fov_id, peak_id, color=channel_name)
    seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

    # determine absolute time index
    times_all = []
    for fov in params['time_table']:
        times_all = np.append(times_all, time_table[fov].keys())
    times_all = np.unique(times_all)
    times_all = np.sort(times_all)
    times_all = np.array(times_all,np.int_)
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():
        # give this cell two lists to hold new information
        Cell.fl_tots = [] # total fluorescence per time point
        Cell.fl_area_avgs = [] # avg fluorescence per unit area by timepoint
        Cell.fl_vol_avgs = [] # avg fluorescence per unit volume by timepoint

        if midline:
            Cell.mid_fl = [] # avg fluorescence of midline

        # and the time points that make up this cell's life
        for n, t in enumerate(Cell.times):
            # create fluorescent image only for this cell and timepoint.
            fl_image_masked = np.copy(fl_stack[t-t0])
            fl_image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # append total flourescent image
            Cell.fl_tots.append(np.sum(fl_image_masked))
            # and the average fluorescence
            Cell.fl_area_avgs.append(np.sum(fl_image_masked) / Cell.areas[n])
            Cell.fl_vol_avgs.append(np.sum(fl_image_masked) / Cell.volumes[n])

            if midline:
                # add the midline average by first applying morphology transform
                bin_mask = np.copy(seg_stack[t-t0])
                bin_mask[bin_mask != Cell.labels[n]] = 0
                med_mask, _ = morphology.medial_axis(bin_mask, return_distance=True)
                # med_mask[med_dist < np.floor(cap_radius/2)] = 0
                # print(img_fluo[med_mask])
                if (np.shape(fl_image_masked[med_mask])[0] > 0):
                    Cell.mid_fl.append(np.nanmean(fl_image_masked[med_mask]))
                else:
                    Cell.mid_fl.append(0)

    # The cell objects in the original dictionary will be updated,
    # no need to return anything specifically.
    return

# find foci using a difference of gaussians method
def foci_analysis(fov_id, peak_id, Cells):
    '''Find foci in cells using a fluorescent image channel.
    This function works on a single peak and all the cells therein.'''

    # make directory for foci debug
    # foci_dir = os.path.join(params['ana_dir'], 'overlay/')
    # if not os.path.exists(foci_dir):
    #     os.makedirs(foci_dir)

    # Import segmented and fluorescenct images
    try:
        image_data_seg = load_stack(fov_id, peak_id, color='seg_unet')
    except IOError:
        image_data_seg = load_stack(fov_id, peak_id, color='seg_otsu')
    image_data_FL = load_stack(fov_id, peak_id,
                               color='sub_{}'.format(params['foci']['foci_plane']))

    # determine absolute time index
    times_all = []
    for fov, times in params['time_table'].items():
        times_all = np.append(times_all, list(times.keys()))
    times_all = np.unique(times_all)
    times_all = np.sort(times_all)
    times_all = np.array(times_all, np.int_)
    t0 = times_all[0] # first time index

    for cell_id, cell in six.iteritems(Cells):

        information('Extracting foci information for %s.' % (cell_id))
        # declare lists holding information about foci.
        disp_l = []
        disp_w = []
        foci_h = []
        # foci_stack = np.zeros((np.size(cell.times),
        #                        image_data_seg[0,:,:].shape[0], image_data_seg[0,:,:].shape[1]))

        # Go through each time point of this cell
        for t in cell.times:
            # retrieve this timepoint and images.
            image_data_temp = image_data_FL[t-t0,:,:]
            image_data_temp_seg = image_data_seg[t-t0,:,:]

            # find foci as long as there is information in the fluorescent image
            if np.sum(image_data_temp) != 0:
                disp_l_tmp, disp_w_tmp, foci_h_tmp = foci_lap(image_data_temp_seg,
                                                              image_data_temp, cell, t)

                disp_l.append(disp_l_tmp)
                disp_w.append(disp_w_tmp)
                foci_h.append(foci_h_tmp)

            # if there is no information, append an empty list.
            # Should this be NaN?
            else:
                disp_l.append([])
                disp_w.append([])
                foci_h.append([])
                # foci_stack[i] = image_data_temp_seg

        # add information to the cell (will replace old data)
        cell.disp_l = disp_l
        cell.disp_w = disp_w
        cell.foci_h = foci_h

        # Create a stack of the segmented images with marked foci
        # This should poentially be changed to the fluorescent images with marked foci
        # foci_stack = np.uint16(foci_stack)
        # foci_stack = np.stack(foci_stack, axis=0)
        # # Export overlaid images
        # foci_filename = params['experiment_name'] + 't%04d_xy%03d_p%04d_r%02d_overlay.tif' % (Cells[cell_id].birth_time, Cells[cell_id].fov, Cells[cell_id].peak, Cells[cell_id].birth_label)
        # foci_filepath = foci_dir + foci_filename
        #
        # tiff.imsave(foci_filepath, foci_stack, compress=3) # save it

        # test
        # sys.exit()

    return

# foci pool (for parallel analysis)
def foci_analysis_pool(fov_id, peak_id, Cells):
    '''Find foci in cells using a fluorescent image channel.
    This function works on a single peak and all the cells therein.'''

    # make directory for foci debug
    # foci_dir = os.path.join(params['ana_dir'], 'overlay/')
    # if not os.path.exists(foci_dir):
    #     os.makedirs(foci_dir)

    # Import segmented and fluorescenct images
    image_data_seg = load_stack(fov_id, peak_id, color='seg_unet')
    image_data_FL = load_stack(fov_id, peak_id,
                               color='sub_{}'.format(params['foci']['foci_plane']))

    # Load time table to determine first image index.
    times_all = np.array(np.sort(params['time_table'][fov_id].keys()), np.int_)
    t0 = times_all[0] # first time index
    tN = times_all[-1] # last time index

    # call foci_cell for each cell object
    pool = Pool(processes=params['num_analyzers'])
    [pool.apply_async(foci_cell(cell_id, cell, t0, image_data_seg, image_data_FL)) for cell_id, cell in six.iteritems(Cells)]
    pool.close()
    pool.join()

# parralel function for each cell
def foci_cell(cell_id, cell, t0, image_data_seg, image_data_FL):
    '''find foci in a cell, single instance to be called by the foci_analysis_pool for parallel processing.
    '''
    disp_l = []
    disp_w = []
    foci_h = []
    # foci_stack = np.zeros((np.size(cell.times),
    #                        image_data_seg[0,:,:].shape[0], image_data_seg[0,:,:].shape[1]))

    # Go through each time point of this cell
    for t in cell.times:
        # retrieve this timepoint and images.
        image_data_temp = image_data_FL[t-t0,:,:]
        image_data_temp_seg = image_data_seg[t-t0,:,:]

        # find foci as long as there is information in the fluorescent image
        if np.sum(image_data_temp) != 0:
            disp_l_tmp, disp_w_tmp, foci_h_tmp = foci_lap(image_data_temp_seg,
                                                          image_data_temp, cell, t)

            disp_l.append(disp_l_tmp)
            disp_w.append(disp_w_tmp)
            foci_h.append(foci_h_tmp)

        # if there is no information, append an empty list.
        # Should this be NaN?
        else:
            disp_l.append(np.nan)
            disp_w.append(np.nan)
            foci_h.append(np.nan)
            # foci_stack[i] = image_data_temp_seg

    # add information to the cell (will replace old data)
    cell.disp_l = disp_l
    cell.disp_w = disp_w
    cell.foci_h = foci_h

# actual worker function for foci detection
def foci_lap(img, img_foci, cell, t):
    '''foci_lap finds foci using a laplacian convolution then fits a 2D
    Gaussian.

    The returned information are the parameters of this Gaussian.
    All the information is returned in the form of np.arrays which are the
    length of the number of found foci across all cells in the image.

    Parameters
    ----------
    img : 2D np.array
        phase contrast or bright field image. Only used for debug
    img_foci : 2D np.array
        fluorescent image with foci.
    cell : cell object
    t : int
        time point to which the images correspond

    Returns
    -------
    disp_l : 1D np.array
        displacement on long axis, in px, of a foci from the center of the cell
    disp_w : 1D np.array
        displacement on short axis, in px, of a foci from the center of the cell
    foci_h : 1D np.array
        Foci "height." Sum of the intensity of the gaussian fitting area.
    '''

    # pull out useful information for just this time point
    i = cell.times.index(t) # find position of the time point in lists (time points may be missing)
    bbox = cell.bboxes[i]
    orientation = cell.orientations[i]
    centroid = cell.centroids[i]
    region = cell.labels[i]

    # declare arrays which will hold foci data
    disp_l = [] # displacement in length of foci from cell center
    disp_w = [] # displacement in width of foci from cell center
    foci_h = [] # foci total amount (from raw image)

    # define parameters for foci finding
    minsig = params['foci']['foci_log_minsig']
    maxsig = params['foci']['foci_log_maxsig']
    thresh = params['foci']['foci_log_thresh']
    peak_med_ratio = params['foci']['foci_log_peak_med_ratio']
    debug_foci = params['foci']['debug_foci']

    # test
    #print ("minsig={:d}  maxsig={:d}  thres={:.4g}  peak_med_ratio={:.2g}".format(minsig,maxsig,thresh,peak_med_ratio))
    # test

    # calculate median cell intensity. Used to filter foci
    img_foci_masked = np.copy(img_foci).astype(np.float)
    img_foci_masked[img != region] = np.nan
    cell_fl_median = np.nanmedian(img_foci_masked)
    cell_fl_mean = np.nanmean(img_foci_masked)

    img_foci_masked[img != region] = 0

    # subtract this value from the cell
    if False:
        img_foci = img_foci.astype('int32') - cell_fl_median.astype('int32')
        img_foci[img_foci < 0] = 0
        img_foci = img_foci.astype('uint16')

    # int_mask = np.zeros(img_foci.shape, np.uint8)
    # avg_int = cv2.mean(img_foci, mask=int_mask)
    # avg_int = avg_int[0]

    # print('median', cell_fl_median)

    # find blobs using difference of gaussian
    over_lap = .95 # if two blobs overlap by more than this fraction, smaller blob is cut
    numsig = (maxsig - minsig + 1) # number of division to consider between min ang max sig
    blobs = blob_log(img_foci_masked, min_sigma=minsig, max_sigma=maxsig,
                     overlap=over_lap, num_sigma=numsig, threshold=thresh)

    # these will hold information about foci position temporarily
    x_blob, y_blob, r_blob = [], [], []
    x_gaus, y_gaus, w_gaus = [], [], []

    # loop through each potential foci
    for blob in blobs:
        yloc, xloc, sig = blob # x location, y location, and sigma of gaus
        xloc = int(np.around(xloc)) # switch to int for slicing images
        yloc = int(np.around(yloc))
        radius = int(np.ceil(np.sqrt(2)*sig)) # will be used to slice out area around foci

        # ensure blob is inside the bounding box
        # this might be better to check if (xloc, yloc) is in regions.coords
        if yloc > np.int16(bbox[0]) and yloc < np.int16(bbox[2]) and xloc > np.int16(bbox[1]) and xloc < np.int16(bbox[3]):

            x_blob.append(xloc) # for plotting
            y_blob.append(yloc) # for plotting
            r_blob.append(radius)

            # cut out a small image from original image to fit gaussian
            gfit_area = img_foci[yloc-radius:yloc+radius, xloc-radius:xloc+radius]
            # gfit_area_0 = img_foci[max(0, yloc-1*radius):min(img_foci.shape[0], yloc+1*radius),
            #                        max(0, xloc-1*radius):min(img_foci.shape[1], xloc+1*radius)]
            gfit_area_fixed = img_foci[yloc-maxsig:yloc+maxsig, xloc-maxsig:xloc+maxsig]

            # fit gaussian to proposed foci in small box
            p = fitgaussian(gfit_area)
            (peak_fit, x_fit, y_fit, w_fit) = p

            # print('peak', peak_fit)
            if x_fit <= 0 or x_fit >= radius*2 or y_fit <= 0 or y_fit >= radius*2:
                if debug_foci: print('Throw out foci (gaus fit not in gfit_area)')
                continue
            elif peak_fit/cell_fl_median < peak_med_ratio:
                if debug_foci: print('Peak does not pass height test.')
                continue
            else:
                # find x and y position relative to the whole image (convert from small box)
                x_rel = int(xloc - radius + x_fit)
                y_rel = int(yloc - radius + y_fit)
                x_gaus = np.append(x_gaus, x_rel) # for plotting
                y_gaus = np.append(y_gaus, y_rel) # for plotting
                w_gaus = np.append(w_gaus, w_fit) # for plotting

                if debug_foci: print('x', xloc, x_rel, x_fit, 'y', yloc, y_rel, y_fit, 'w', sig, radius, w_fit, 'h', np.sum(gfit_area), np.sum(gfit_area_fixed), peak_fit)

                # calculate distance of foci from middle of cell (scikit image)
                if orientation < 0:
                    orientation = np.pi+orientation
                disp_y = (y_rel-centroid[0])*np.sin(orientation) - (x_rel-centroid[1])*np.cos(orientation)
                disp_x = (y_rel-centroid[0])*np.cos(orientation) + (x_rel-centroid[1])*np.sin(orientation)

                # append foci information to the list
                disp_l = np.append(disp_l, disp_y)
                disp_w = np.append(disp_w, disp_x)
                foci_h = np.append(foci_h, np.sum(gfit_area_fixed))
                # foci_h = np.append(foci_h, peak_fit)
        else:
            if debug_foci:
                print ('Blob not in bounding box.')

    # draw foci on image for quality control
    if debug_foci:
        outputdir = os.path.join(params['ana_dir'], 'debug_foci')
        if not os.path.isdir(outputdir):
            os.makedirs(outputdir)

        # print(np.min(gfit_area), np.max(gfit_area), gfit_median, avg_int, peak)
        # processing of image
        fig = plt.figure(figsize=(12,12))
        ax = fig.add_subplot(1,5,1)
        plt.title('fluor image')
        plt.imshow(img_foci, interpolation='nearest', cmap='gray')
        ax = fig.add_subplot(1,5,2)
        ax.set_title('segmented image')
        ax.imshow(img, interpolation='nearest', cmap='gray')

        ax = fig.add_subplot(1,5,3)
        ax.set_title('DoG blobs')
        ax.imshow(img_foci, interpolation='nearest', cmap='gray')
        # add circles for where the blobs are
        for i, spot in enumerate(x_blob):
            foci_center = Ellipse([x_blob[i], y_blob[i]], r_blob[i], r_blob[i],
                                  color=(1.0, 1.0, 0), linewidth=2, fill=False, alpha=0.5)
            ax.add_patch(foci_center)

        # show the shape of the gaussian for recorded foci
        ax = fig.add_subplot(1,5,4)
        ax.set_title('final foci')
        ax.imshow(img_foci, interpolation='nearest', cmap='gray')
        # print foci that pass and had gaussians fit
        for i, spot in enumerate(x_gaus):
            foci_ellipse = Ellipse([x_gaus[i], y_gaus[i]], w_gaus[i], w_gaus[i],
                                    color=(0, 1.0, 0.0), linewidth=2, fill=False, alpha=0.5)
            ax.add_patch(foci_ellipse)

        ax = fig.add_subplot(1,5,5)
        ax.set_title('overlay')
        ax.imshow(img, interpolation='nearest', cmap='gray')
        # print foci that pass and had gaussians fit
        for i, spot in enumerate(x_gaus):
            foci_ellipse = Ellipse([x_gaus[i], y_gaus[i]], 3, 3,
                                    color=(1.0, 1.0, 0), linewidth=2, fill=False, alpha=0.5)
            ax.add_patch(foci_ellipse)

        #plt.show()
        filename = 'foci_' + cell.id + '_time{:04d}'.format(t) + '.pdf'
        fileout = os.path.join(outputdir,filename)
        fig.savefig(fileout, bbox_inches='tight', pad_inches=0)
        print (fileout)
        plt.close('all')
        nblobs = len(blobs)
        print ("nblobs = {:d}".format(nblobs))

    return disp_l, disp_w, foci_h

def update_cell_foci(cells, foci):
    '''Updates cells' .foci attribute in-place using information
    in foci dictionary
    '''
    for focus_id, focus in foci.items():
        for cell in focus.cells:

            cell_id = cell.id
            cells[cell_id].foci[focus_id] = focus

# finds best fit for 2d gaussian using functin above
def fitgaussian(data):
    """Returns (height, x, y, width_x, width_y)
    the gaussian parameters of a 2D distribution found by a fit
    if params are not provided, they are calculated from the moments
    params should be (height, x, y, width_x, width_y)"""
    gparams = moments(data) # create guess parameters.
    errorfunction = lambda p: np.ravel(gaussian(*p)(*np.indices(data.shape)) - data)
    p, success = leastsq(errorfunction, gparams)
    return p

# calculate dice coefficient for two blobs
def dice_coeff_foci(mask_1_f, mask_2_f):
    '''Accepts two flattened numpy arrays from
    binary masks of two blobs and compares them
    using the dice metric.

    Returns a single dice score.
    '''
    intersection = np.sum(mask_1_f * mask_2_f)
    score = (2. * intersection) / (np.sum(mask_1_f) + np.sum(mask_2_f))
    return score

# returnes a 2D gaussian function
def gaussian(height, center_x, center_y, width):
    '''Returns a gaussian function with the given parameters. It is a circular gaussian.
    width is 2*sigma x or y
    '''
    # return lambda x,y: height*np.exp(-(((center_x-x)/width_x)**2+((center_y-y)/width_y)**2)/2)
    return lambda x,y: height*np.exp(-(((center_x-x)/width)**2+((center_y-y)/width)**2)/2)

# moments of a 2D gaussian
def moments(data):
    '''
    Returns (height, x, y, width_x, width_y)
    The (circular) gaussian parameters of a 2D distribution by calculating its moments.
    width_x and width_y are 2*sigma x and sigma y of the guassian.
    '''
    total = data.sum()
    X, Y = np.indices(data.shape)
    x = (X*data).sum()/total
    y = (Y*data).sum()/total
    col = data[:, int(y)]
    width = float(np.sqrt(abs((np.arange(col.size)-y)**2*col).sum()/col.sum()))
    row = data[int(x), :]
    # width_y = np.sqrt(abs((np.arange(row.size)-x)**2*row).sum()/row.sum())
    height = data.max()
    return height, x, y, width

# returns a 1D gaussian function
def gaussian1d(x, height, mean, sigma):
    '''
    x : data
    height : height
    mean : center
    sigma : RMS width
    '''
    return height * np.exp(-(x-mean)**2 / (2*sigma**2))

# analyze ring fluroescence.
def ring_analysis(fov_id, peak_id, Cells, ring_plane='c2'):
    '''Add information to the Cell objects about the location of the Z ring. Sums the fluorescent channel along the long axis of the cell. This can be plotted directly to give a good idea about the development of the ring. Also fits a gaussian to the profile.

    Parameters
    ----------
    fov_id : int
        FOV number of the lineage to analyze.
    peak_id : int
        Peak number of the lineage to analyze.
    Cells : dict of Cell objects (from a Lineages dictionary)
        Cells should be prefiltered to match fov_id and peak_id.
    ring_plane : str
        The suffix of the channel to analyze. 'c1', 'c2', 'sub_c2', etc.

    Usage
    -----
    for fov_id, peaks in Lineages.iteritems():
        for peak_id, Cells in peaks.iteritems():
            mm3.ring_analysis(fov_id, peak_id, Cells, ring_plane='sub_c2')
    '''

    peak_width_guess = 2

    # Load data
    ring_stack = load_stack(fov_id, peak_id, color=ring_plane)
    seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

    # Load time table to determine first image index.
    time_table = load_time_table()
    times_all = np.array(np.sort(time_table[fov_id].keys()), np.int_)
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():

        # initialize ring data arrays for cell
        Cell.ring_locs = []
        Cell.ring_heights = []
        Cell.ring_widths = []
        Cell.ring_medians = []
        Cell.ring_profiles = []

        # loop through each time point for this cell
        for n, t in enumerate(Cell.times):
            # Make mask of fluorescent channel using segmented image
            ring_image_masked = np.copy(ring_stack[t-t0])
            ring_image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # Sum along long axis, use the profile_line function from skimage
            # Use orientation of cell as calculated from the ellipsoid fit,
            # the known length of the cell from the feret diameter,
            # and a width that is greater than the cell width.

            # find endpoints of line
            centroid = Cell.centroids[n]
            orientation = Cell.orientations[n]
            length = Cell.lengths[n]
            width = Cell.widths[n] * 1.25

            # give 2 pixel buffer to each end to capture area outside cell.
            p1 = (centroid[0] - np.sin(orientation) * (length+4)/2,
                  centroid[1] - np.cos(orientation) * (length+4)/2)
            p2 = (centroid[0] + np.sin(orientation) * (length+4)/2,
                  centroid[1] + np.cos(orientation) * (length+4)/2)

            # ensure old pole is always first point
            if p1[0] > p2[0]:
                p1, p2 = p2, p1 # python is cool

            profile = profile_line(ring_image_masked, p1, p2, linewidth=width,
                                   order=1, mode='constant', cval=0)
            profile_indicies = np.arange(len(profile))

            # subtract median from profile, using non-zero values for median
            profile_median = np.median(profile[np.nonzero(profile)])
            profile_sub = profile - profile_median
            profile_sub[profile_sub < 0] = 0

            # find peak position simply using maximum.
            peak_index = np.argmax(profile)
            peak_height = profile[peak_index]
            peak_height_sub = profile_sub[peak_index]

            try:
                # Fit gaussian
                p_guess = [peak_height_sub, peak_index, peak_width_guess]
                popt, pcov = curve_fit(gaussian1d, profile_indicies,
                                       profile_sub, p0=p_guess)

                peak_width = popt[2]
            except:
                # information('Ring gaussian fit failed. {} {} {}'.format(fov_id, peak_id, t))
                peak_width = np.float('NaN')

            # Add data to cells
            Cell.ring_locs.append(peak_index - 3) # minus 3 because we added 2 before and line_profile adds 1.
            Cell.ring_heights.append(peak_height)
            Cell.ring_widths.append(peak_width)
            Cell.ring_medians.append(profile_median)
            Cell.ring_profiles.append(profile) # append whole profile

    return

# Calculate Y projection intensity of a fluorecent channel per cell
def profile_analysis(fov_id, peak_id, Cells, profile_plane='c2'):
    '''Calculate profile of plane along cell and add information to Cell object. Sums the fluorescent channel along the long axis of the cell.

    Parameters
    ----------
    fov_id : int
        FOV number of the lineage to analyze.
    peak_id : int
        Peak number of the lineage to analyze.
    Cells : dict of Cell objects (from a Lineages dictionary)
        Cells should be prefiltered to match fov_id and peak_id.
    profile_plane : str
        The suffix of the channel to analyze. 'c1', 'c2', 'sub_c2', etc.

    Usage
    -----

    '''

    # Load data
    fl_stack = load_stack(fov_id, peak_id, color=profile_plane)
    seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

    # Load time table to determine first image index.
    # load_time_table()
    times_all = []
    for fov in params['time_table']:
        times_all = np.append(times_all, list(params['time_table'][fov].keys()))
    times_all = np.unique(times_all)
    times_all = np.sort(times_all)
    times_all = np.array(times_all,np.int_)
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():

        # initialize ring data arrays for cell
        fl_profiles = []

        # loop through each time point for this cell
        for n, t in enumerate(Cell.times):
            # Make mask of fluorescent channel using segmented image
            image_masked = np.copy(fl_stack[t-t0])
            image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # Sum along long axis, use the profile_line function from skimage
            # Use orientation of cell as calculated from the ellipsoid fit,
            # the known length of the cell from the feret diameter,
            # and a width that is greater than the cell width.

            # find endpoints of line
            centroid = Cell.centroids[n]
            orientation = Cell.orientations[n]
            length = Cell.lengths[n]
            width = Cell.widths[n] * 1.25

            # give 2 pixel buffer to each end to capture area outside cell.
            p1 = (centroid[0] - np.sin(orientation) * (length+4)/2,
                  centroid[1] - np.cos(orientation) * (length+4)/2)
            p2 = (centroid[0] + np.sin(orientation) * (length+4)/2,
                  centroid[1] + np.cos(orientation) * (length+4)/2)

            # ensure old pole is always first point
            if p1[0] > p2[0]:
                p1, p2 = p2, p1 # python is cool

            profile = profile_line(image_masked, p1, p2, linewidth=width,
                                   order=1, mode='constant', cval=0)

            fl_profiles.append(profile)

        # append whole profile, using plane name
        setattr(Cell, 'fl_profiles_'+profile_plane, fl_profiles)

    return

# Calculate X projection at midcell and quarter position
def x_profile_analysis(fov_id, peak_id, Cells, profile_plane='sub_c2'):
    '''Calculate profile of plane along cell and add information to Cell object. Sums the fluorescent channel along the long axis of the cell.

    Parameters
    ----------
    fov_id : int
        FOV number of the lineage to analyze.
    peak_id : int
        Peak number of the lineage to analyze.
    Cells : dict of Cell objects (from a Lineages dictionary)
        Cells should be prefiltered to match fov_id and peak_id.
    profile_plane : str
        The suffix of the channel to analyze. 'c1', 'c2', 'sub_c2', etc.

    '''

    # width to sum over in pixels
    line_width = 6

    # Load data
    fl_stack = load_stack(fov_id, peak_id, color=profile_plane)
    seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

    # Load time table to determine first image index.
    time_table = load_time_table()
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():

        # print(Cell.id)

        # initialize data arrays for cell
        midcell_fl_profiles = []
        midcell_pts = []
        quarter_fl_profiles = []
        quarter_pts = []

        # loop through each time point for this cell
        for n, t in enumerate(Cell.times):
            # Make mask of fluorescent channel using segmented image
            image_masked = np.copy(fl_stack[t-t0])
            # image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # Sum along short axis, use the profile_line function from skimage
            # Use orientation of cell as calculated from the ellipsoid fit,
            # the known length of the cell from the feret diameter,
            # and a width that is greater than the cell width.

            # find end points for summing
            centroid = Cell.centroids[n]
            orientation = Cell.orientations[n]
            length = Cell.lengths[n]
            width = Cell.widths[n]

            # midcell
            # give 2 pixel buffer to each end to capture area outside cell.
            md_p1 = (centroid[0] - np.cos(orientation) * (width+8)/2,
                     centroid[1] - np.sin(orientation) * (width+8)/2)
            md_p2 = (centroid[0] + np.cos(orientation) * (width+8)/2,
                     centroid[1] + np.sin(orientation) * (width+8)/2)

            # ensure lower x point is always first
            if md_p1[1] > md_p2[1]:
                md_p1, md_p2 = md_p2, md_p1 # python is cool
            midcell_pts.append((md_p1, md_p2))

            # print(t, centroid, orientation, md_p1, md_p2)
            md_profile = profile_line(image_masked, md_p1, md_p2,
                                      linewidth=line_width,
                                      order=1, mode='constant', cval=0)
            midcell_fl_profiles.append(md_profile)

            # quarter position, want to measure at mother end
            if orientation > 0:
                yq = centroid[0] - np.sin(orientation) * 0.5 * (length * 0.5)
                xq = centroid[1] + np.cos(orientation) * 0.5 * (length * 0.5)
            else:
                yq = centroid[0] + np.sin(orientation) * 0.5 * (length * 0.5)
                xq = centroid[1] - np.cos(orientation) * 0.5 * (length * 0.5)

            q_p1 = (yq - np.cos(orientation) * (width+8)/2,
                    xq - np.sin(orientation) * (width+8)/2)
            q_p2 = (yq + np.cos(orientation) * (width+8)/2,
                    xq + np.sin(orientation) * (width+8)/2)

            if q_p1[1] > q_p2[1]:
                q_p1, q_p2 = q_p2, q_p1
            quarter_pts.append((q_p1, q_p2))

            q_profile = profile_line(image_masked, q_p1, q_p2,
                                     linewidth=line_width,
                                     order=1, mode='constant', cval=0)
            quarter_fl_profiles.append(q_profile)

        # append whole profile, using plane name
        setattr(Cell, 'fl_md_profiles_'+profile_plane, midcell_fl_profiles)
        setattr(Cell, 'midcell_pts', midcell_pts)
        setattr(Cell, 'fl_quar_profiles_'+profile_plane, quarter_fl_profiles)
        setattr(Cell, 'quarter_pts', quarter_pts)

    return

# Calculate X projection at midcell and quarter position
def constriction_analysis(fov_id, peak_id, Cells, plane='sub_c1'):
    '''Calculate profile of plane along cell and add information to Cell object. Sums the fluorescent channel along the long axis of the cell.

    Parameters
    ----------
    fov_id : int
        FOV number of the lineage to analyze.
    peak_id : int
        Peak number of the lineage to analyze.
    Cells : dict of Cell objects (from a Lineages dictionary)
        Cells should be prefiltered to match fov_id and peak_id.
    plane : str
        The suffix of the channel to analyze. 'c1', 'c2', 'sub_c2', etc.

    '''

    # Load data
    sub_stack = load_stack(fov_id, peak_id, color=plane)
    seg_stack = load_stack(fov_id, peak_id, color='seg_unet')

    # Load time table to determine first image index.
    time_table = load_time_table()
    t0 = times_all[0] # first time index

    # Loop through cells
    for Cell in Cells.values():

        # print(Cell.id)

        # initialize data arrays for cell
        midcell_imgs = [] # Just a small image of the midcell
        midcell_sums = [] # holds sum of pixel values in midcell area
        midcell_vars = [] # variances

        coeffs_2nd = [] # coeffiients for fitting

        # loop through each time point for this cell
        for n, t in enumerate(Cell.times):
            # Make mask of subtracted image
            image_masked = np.copy(sub_stack[t-t0])
            image_masked[seg_stack[t-t0] != Cell.labels[n]] = 0

            # make a box aroud the midcell from which to calculate stats
            centroid = Cell.centroids[n]
            orientation = Cell.orientations[n]
            length = Cell.lengths[n]
            slice_l = np.around(length/4).astype('int')
            width = Cell.widths[n]
            slice_w = np.around(width/2).astype('int') + 3
            slice_l = slice_w

            # rotate box and then slice out area around centroid
            if orientation > 0:
                rot_angle = 90 - orientation * (180 / np.pi)
            else:
                rot_angle = -90 - orientation * (180 / np.pi)

            rotated = rotate(image_masked, rot_angle, resize=False,
                             center=centroid, mode='constant', cval=0)
            centroid = [int(coord) for coord in centroid]
            cropped_md = rotated[centroid[0]-slice_l:centroid[0]+slice_l,
                                 centroid[1]-slice_w:centroid[1]+slice_w]

            # sum across with widths
            md_widths = np.array([np.around(sum(row),5) for row in cropped_md])

            # fit widths
            x_pixels = np.arange(1, len(md_widths)+1) - (len(md_widths)+1)/2
            p_guess = (1, 1, 1)
            popt, pcov = curve_fit(poly2o, x_pixels, md_widths, p0=p_guess)
            a, b, c = popt
            # save coefficients
            coeffs_2nd.append(a)

            # go backwards through coeeficients and find at which index the coeff becomes negative.
            constriction_index = None
            for i, coeff in enumerate(reversed(coeffs_2nd), start=0):
                if coeff < 0:
                    constriction_index = i
                    break

            # fix index
            if constriction_index == None:
                constriction_index = len(coeffs_2nd) - 1 # make it last point if it was not found
            else:
                constriction_index = len(coeffs_2nd) - constriction_index - 1

            # midcell_imgs.append(cropped_md)
            # midcell_sums.append(np.sum(cropped_md))
            # midcell_vars.append(np.var(cropped_md))

        # append whole profile, using plane name
        # setattr(Cell, 'md_image_'+plane, midcell_imgs)
        # setattr(Cell, 'md_sums', midcell_sums)
        # setattr(Cell, 'md_vars', midcell_vars)

        setattr(Cell, 'constriction_time', Cell.times[constriction_index])

    return

# Calculate pole age of cell and add as attribute
def calculate_pole_age(Cells):
    '''Finds the pole age of each end of the cell. Adds this information to the cell object.

    This should maybe move to helpers
    '''

    # run through once and set up default
    for cell_id, cell_tmp in six.iteritems(Cells):
        cell_tmp.poleage = None

    for cell_id, cell_tmp in six.iteritems(Cells):
        # start from r1 cells which have r1 parents in the list.
        # these cells are old pole mothers.
    #     if cell_tmp.parent in Cells and cell_tmp.birth_label == 1:

        # less stringent requirement that the cell just r1
        if cell_tmp.birth_label == 1:

            # label this cell
            cell_tmp.poleage = (1000, 0) # closed end age first, 1000 for old pole.

            # label the daughter cell 01 if it is in the list
            if cell_tmp.daughters[1] in Cells:
                # sets poleage of this cell and recursively goes through descendents.
                Cells = set_poleages(cell_tmp.daughters[1], 1, Cells)

    return Cells

def set_poleages(cell_id, daughter_index, Cells):
    '''Determines pole ages for cells. Only for cells which are not old-pole mother.'''

    parent_poleage = Cells[Cells[cell_id].parent].poleage

    # the lower daughter
    if daughter_index == 0:
        Cells[cell_id].poleage = (parent_poleage[0]+1, 0)
    elif daughter_index == 1:
        Cells[cell_id].poleage = (0, parent_poleage[1]+1)

    for i, daughter_id in enumerate(Cells[cell_id].daughters):
        if daughter_id in Cells:
            Cells = set_poleages(daughter_id, i, Cells)

    return Cells

def poly2o(x, a, b, c):
    '''Second order polynomial of the form
       y = a*x^2 + bx + c'''

    return a*x**2 + b*x + c

def nd2ToTIFF(params):
    '''
    This script converts a Nikon Elements .nd2 file to individual TIFF files per time point. Multiple color planes are stacked in each time point to make a multipage TIFF.
    '''

    # Load the project parameters file
    information('Loading experiment parameters.')
    print("ND2TOTIFF")
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    # number of rows of channels. Used for cropping.
    number_of_rows = p['nd2ToTIFF']['number_of_rows']

    # cropping
    vertical_crop = False
    if number_of_rows == 1 and p['nd2ToTIFF']['crop_ymin'] and p['nd2ToTIFF']['crop_ymax']:
        try:
            # This is for percentage crop
            vertical_crop = [p['nd2ToTIFF']['crop_ymin'], p['nd2ToTIFF']['crop_ymax']]
        except KeyError:
            pass
    elif number_of_rows == 2:
        # if there is more than one row, make a list of pairs
        # [[y1_min, y1_max], [y2_min, y2_max]]
        vertical_crop = p['nd2ToTIFF']['2row_crop']

    # number between 0 and 9, 0 is no compression, 9 is most compression.
    tif_compress = p['nd2ToTIFF']['tiff_compress']

    # set up image and analysis folders if they do not already exist
    if not os.path.exists(p['TIFF_dir']):
        os.makedirs(p['TIFF_dir'])

    # Load ND2 files into a list for processing
    if p['nd2ToTIFF']['external_directory']:
        nd2files = glob.glob(os.path.join(p['nd2ToTIFF']['external_directory'], "*.nd2"))
        information("Found %d files to analyze from external directory." % len(nd2files))
    else:
        information("Experiment directory: {:s}".format(p['experiment_directory']))
        nd2files = glob.glob(os.path.join(p['experiment_directory'], "*.nd2"))
        information("Found %d files to analyze in experiment directory." % len(nd2files))

    for nd2_file in nd2files:
        file_prefix = os.path.split(os.path.splitext(nd2_file)[0])[1]
        information('Extracting %s ...' % file_prefix)

        # load the nd2. the nd2f file object has lots of information thanks to pims
        with pims_nd2.ND2_Reader(nd2_file) as nd2f:
            try:
                starttime = nd2f.metadata['time_start_jdn'] # starttime is jd
                information('Starttime got from nd2 metadata.')
            except ValueError:
                # problem with the date
                jdn = julian_day_number()
                nd2f._lim_metadata_desc.dTimeStart = jdn
                starttime = nd2f.metadata['time_start_jdn'] # starttime is jd
                information('Starttime found from lim.')

            # get the color names out. Kinda roundabout way.
            planes = [nd2f.metadata[md]['name'] for md in nd2f.metadata if md[0:6] == u'plane_' and not md == u'plane_count']

            # this insures all colors will be saved when saving tiff
            if len(planes) > 1:
                nd2f.bundle_axes = [u'c', u'y', u'x']

            # extraction range is the time points that will be taken out. Note the indexing,
            # it is zero indexed to grab from nd2, but TIFF naming starts at 1.
            # if there is more than one FOV (len(nd2f) != 1), make sure the user input
            # last time index is before the actual time index. Ignore it.
            if (p['nd2ToTIFF']['image_start'] < 1):
                p['nd2ToTIFF']['image_start'] = 1
            if p['nd2ToTIFF']['image_end'] == 'None':
                p['nd2ToTIFF']['image_end'] = False
            if p['nd2ToTIFF']['image_end']:
                if len(nd2f) > 1 and len(nd2f) < p['nd2ToTIFF']['image_end']:
                    p['nd2ToTIFF']['image_end'] = len(nd2f)
            else:
                p['nd2ToTIFF']['image_end'] = len(nd2f)
            extraction_range = range(p['nd2ToTIFF']['image_start'],
                                     p['nd2ToTIFF']['image_end']+1)

            # loop through time points
            for t in extraction_range:
                # timepoint output name (1 indexed rather than 0 indexed)
                t_id = t - 1
                # set counter for FOV output name
                #fov = fov_naming_start

                for fov_id in range(0, nd2f.sizes[u'm']): # for every FOV
                    # fov_id is the fov index according to elements, fov is the output fov ID
                    fov = fov_id + 1

                    # skip FOVs as specified above
                    if len(user_spec_fovs) > 0 and not (fov in user_spec_fovs):
                        continue

                    # set the FOV we are working on in the nd2 file object
                    nd2f.default_coords[u'm'] = fov_id

                    # get time picture was taken
                    seconds = copy.deepcopy(nd2f[t_id].metadata['t_ms']) / 1000.
                    minutes = seconds / 60.
                    hours = minutes / 60.
                    days = hours / 24.
                    acq_time = starttime + days

                    # get physical location FOV on stage
                    x_um = nd2f[t_id].metadata['x_um']
                    y_um = nd2f[t_id].metadata['y_um']

                    # make dictionary which will be the metdata for this TIFF
                    metadata_t = { 'fov': fov,
                                   't' : t,
                                   'jd': acq_time,
                                   'x': x_um,
                                   'y': y_um,
                                   'planes': planes}
                    metadata_json = json.dumps(metadata_t)

                    # get the pixel information
                    image_data = nd2f[t_id]

                    # crop tiff if specified. Lots of flags for if there are double rows or  multiple colors
                    if vertical_crop:
                        # add extra axis to make below slicing simpler.
                        if len(image_data.shape) < 3:
                            image_data = np.expand_dims(image_data, axis=0)

                        # for just a simple crop
                        if number_of_rows == 1:

                            nc, H, W = image_data.shape
                            ylo = int(vertical_crop[0]*H)
                            yhi = int(vertical_crop[1]*H)
                            image_data = image_data[:, ylo:yhi, :]

                            # save the tiff
                            tif_filename = file_prefix + "_t%04dxy%02d.tif" % (t, fov)
                            information('Saving %s.' % tif_filename)
                            tiff.imsave(os.path.join(p['TIFF_dir'], tif_filename), image_data, description=metadata_json, compress=tif_compress, photometric='minisblack')

                        # for dealing with two rows of channel
                        elif number_of_rows == 2:
                            # cut and save top row
                            image_data_one = image_data[:,vertical_crop[0][0]:vertical_crop[0][1],:]
                            tif_filename = file_prefix + "_t%04dxy%02d_1.tif" % (t, fov)
                            information('Saving %s.' % tif_filename)
                            tiff.imsave(os.path.join(p['TIFF_dir'], tif_filename), image_data_one, description=metadata_json, compress=tif_compress, photometric='minisblack')

                            # cut and save bottom row
                            metadata_t['fov'] = fov # update metdata
                            metadata_json = json.dumps(metadata_t)
                            image_data_two = image_data[:,vertical_crop[1][0]:vertical_crop[1][1],:]
                            tif_filename = file_prefix + "_t%04dxy%02d_2.tif" % (t, fov)
                            information('Saving %s.' % tif_filename)
                            tiff.imsave(os.path.join(p['TIFF_dir'], tif_filename), image_data_two, description=metadata_json, compress=tif_compress, photometric='minisblack')

                    else: # just save the image if no cropping was done.
                        tif_filename = file_prefix + "_t%04dxy%02d.tif" % (t, fov)
                        information('Saving %s.' % tif_filename)
                        tiff.imsave(os.path.join(p['TIFF_dir'], tif_filename), image_data, description=metadata_json, compress=tif_compress, photometric='minisblack')

                    # increase FOV counter
                    fov += 1

def compile(params):
    '''mm3_Compile.py locates and slices out mother machine channels into image stacks.'''

    # Load the project parameters file
    information('Loading experiment parameters.')
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    information('Using {} threads for multiprocessing.'.format(p['num_analyzers']))

    # only analyze images up until this t point. Put in None otherwise
    if 't_end' in p['compile']:
        t_end = p['compile']['t_end']
        if t_end == 'None':
            t_end = None
    else:
        t_end = None
    # only analyze images at and after this t point. Put in None otherwise
    if 't_start' in p['compile']:
        t_start = p['compile']['t_start']
        if t_start == 'None':
            t_start = None
    else:
        t_start = None

    # create the subfolders if they don't
    if not os.path.exists(p['ana_dir']):
        os.makedirs(p['ana_dir'])
    if p['output'] == 'TIFF':
        if not os.path.exists(p['chnl_dir']):
            os.makedirs(p['chnl_dir'])
    elif p['output'] == 'HDF5':
        if not os.path.exists(p['hdf5_dir']):
            os.makedirs(p['hdf5_dir'])

    # declare information variables
    analyzed_imgs = {} # for storing get_params pool results.

    ### process TIFFs for metadata #################################################################
    if not p['compile']['do_metadata']:
        information("Loading image parameters dictionary.")

        with open(os.path.join(p['ana_dir'], 'TIFF_metadata.pkl'), 'rb') as tiff_metadata:
            analyzed_imgs = pickle.load(tiff_metadata)

    else:
        information("Finding image parameters.")

        # get all the TIFFs in the folder
        found_files = glob.glob(os.path.join(p['TIFF_dir'],'*.tif')) # get all tiffs
        found_files = [filepath.split('/')[-1] for filepath in found_files] # remove pre-path
        found_files = sorted(found_files) # should sort by timepoint

        # keep images starting at this timepoint
        if t_start is not None:
            information('Removing images before time {}'.format(t_start))
            # go through list and find first place where timepoint is equivalent to t_start
            for n, ifile in enumerate(found_files):
                string = re.compile('t{:0=3}xy|t{:0=4}xy'.format(t_start,t_start)) # account for 3 and 4 digit
                # if re.search == True then a match was found
                if re.search(string, ifile):
                    # cut off every file name prior to this one and quit the loop
                    found_files = found_files[n:]
                    break

        # remove images after this timepoint
        if t_end is not None:
            information('Removing images after time {}'.format(t_end))
            # go through list and find first place where timepoint is equivalent to t_end
            for n, ifile in enumerate(found_files):
                string = re.compile('t%03dxy|t%04dxy' % (t_end, t_end)) # account for 3 and 4 digit
                if re.search(string, ifile):
                    found_files = found_files[:n]
                    break


        # if user has specified only certain FOVs, filter for those
        if (len(user_spec_fovs) > 0):
            information('Filtering TIFFs by FOV.')
            fitered_files = []
            for fov_id in user_spec_fovs:
                fov_string = 'xy%02d' % fov_id # xy01
                fitered_files += [ifile for ifile in found_files if fov_string in ifile]

            found_files = fitered_files[:]

        # get information for all these starting tiffs
        if len(found_files) > 0:
            information("Found %d image files." % len(found_files))
        else:
            warning('No TIFF files found')

        if p['compile']['find_channels_method'] == 'peaks':

            # initialize pool for analyzing image metadata
            pool = Pool(p['num_analyzers'])

            # loop over images and get information
            for fn in found_files:
                # get_params gets the image metadata and puts it in analyzed_imgs dictionary
                # for each file name. True means look for channels

                # This is the non-parallelized version (useful for debug)
                # analyzed_imgs[fn] = get_tif_params(fn, True)

                # Parallelized
                analyzed_imgs[fn] = pool.apply_async(get_tif_params, args=(fn, True))

            information('Waiting for image analysis pool to be finished.')

            pool.close() # tells the process nothing more will be added.
            pool.join() # blocks script until everything has been processed and workers exit

            information('Image analysis pool finished, getting results.')

            # get results from the pool and put them in a dictionary
            for fn in analyzed_imgs.keys():
                result = analyzed_imgs[fn]
                if result.successful():
                    analyzed_imgs[fn] = result.get() # put the metadata in the dict if it's good
                else:
                    analyzed_imgs[fn] = False # put a false there if it's bad

        elif p['compile']['find_channels_method'] == 'Unet':
            # Use Unet trained on trap and central channel locations to locate, crop, and align traps
            information("Identifying channel locations and aligning images using U-net.")

            # load model to pass to algorithm
            information("Loading model...")

            # if namespace.modelfile:
            #     model_file_path = namespace.modelfile
            # else:
            model_file_path = p['compile']['model_file_traps']
            # *** Need parameter for weights
            model = models.load_model(model_file_path, custom_objects={'tversky_loss': tversky_loss,'cce_tversky_loss': cce_tversky_loss})
            information("Model loaded.")

            # initialize pool for getting image metadata
            pool = Pool(p['num_analyzers'])

            # loop over images and get information
            for fn in found_files:
                # get_params gets the image metadata and puts it in analyzed_imgs dictionary
                # for each file name. Won't look for channels, just gets the metadata for later use by Unet

                # This is the non-parallelized version (useful for debug)
                # analyzed_imgs[fn] = get_initial_tif_params(fn)

                # Parallelized
                analyzed_imgs[fn] = pool.apply_async(get_initial_tif_params, args=(fn,))

            information('Waiting for image metadata pool to be finished.')
            pool.close() # tells the process nothing more will be added.
            pool.join() # blocks script until everything has been processed and workers exit

            information('Image metadata pool finished, getting results.')

            # get results from the pool and put them in a dictionary
            for fn in analyzed_imgs.keys():
               result = analyzed_imgs[fn]
               if result.successful():
                   analyzed_imgs[fn] = result.get() # put the metadata in the dict if it's good
               else:
                   analyzed_imgs[fn] = False # put a false there if it's bad

            # print(analyzed_imgs)

            # set up some variables for Unet and image aligment/cropping
            file_names = [key for key in analyzed_imgs.keys()]
            file_names.sort() # sort the file names by time
            file_names = np.asarray(file_names)
            fov_ids = [analyzed_imgs[key]['fov'] for key in analyzed_imgs.keys()]

            unique_fov_ids = np.unique(fov_ids)

            if p['compile']['do_channel_masks']:
                channel_masks = {}

            for fov_id in unique_fov_ids:

                information('Performing trap segmentation for fov_id: {}'.format(fov_id))
                #print(analyzed_imgs)
                fov_indices = np.where(fov_ids == fov_id)[0]
                # print(fov_indices)
                fov_file_names = [file_names[idx] for idx in fov_indices]
                trap_align_metadata = {'first_frame_name': fov_file_names[0],
                                    'frame_count': len(fov_file_names),
                                    'plane_number': len(analyzed_imgs[fn]['planes']),
                                    'trap_height': p['compile']['trap_crop_height'],
                                    'trap_width': p['compile']['trap_crop_width'],
                                    'phase_plane': p['phase_plane'],
                                    'phase_plane_index': p['moviemaker']['phase_plane_index'],
                                    'shift_distance': 256,
                                    'full_frame_size': 2048}

                dilator = np.ones((1,300))

                # create weights for taking weighted mean of several runs of Unet over various crops of the first image in the series. This helps remove "blind spots" from the neural network at the edges of each crop of the original image.
                stack_weights = get_weights_array(np.zeros((trap_align_metadata['full_frame_size'],trap_align_metadata['full_frame_size'])), trap_align_metadata['shift_distance'], subImageNumber=16, padSubImageNumber=25)[0,...]
                # print(stack_weights.shape) #uncomment for debugging

                # get prediction of where traps are located in first image
                imgPath = os.path.join(p['experiment_directory'], p['image_directory'],
                                       trap_align_metadata['first_frame_name'])
                img = io.imread(imgPath)
                # detect if there are multiple imaging channels, and rearrange image if necessary, keeping only the phase image
                img = permute_image(img, trap_align_metadata)
                if p['debug']:
                    io.imshow(img/np.max(img))
                    plt.title("Initial phase image")
                    plt.show()

                # produces predition stack with 3 "pages", index 0 is for traps, index 1 is for central tough, index 2 is for background
                information("Predicting trap locations for first frame.")
                first_frame_trap_prediction = get_frame_predictions(img,
                                                                        model,
                                                                        stack_weights,
                                                                        trap_align_metadata['shift_distance'],
                                                                        subImageNumber=16,
                                                                        padSubImageNumber=25,
                                                                        debug=p['debug'])

                if p['debug']:
                    fig,ax = plt.subplots(nrows=1, ncols=4, figsize=(12,12))
                    ax[0].imshow(img)
                    for i in range(first_frame_trap_prediction.shape[-1]):
                        ax[i+1].imshow(first_frame_trap_prediction[:,:,i])
                    plt.show()

                # flatten prediction stack such that each pixel of the resulting 2D image is the index of the prediction image above with the highest predicted probability
                class_predictions = np.argmax(first_frame_trap_prediction, axis=2)

                traps = class_predictions == 0 # returns boolean array where our intial guesses at trap locations are True

                if p['debug']:
                    io.imshow(traps)
                    plt.title('Initial trap masks')
                    plt.show()

                trap_labels = measure.label(traps)
                trap_props = measure.regionprops(trap_labels)

                trap_area_threshold = p['compile']['trap_area_threshold']
                trap_bboxes = get_frame_trap_bounding_boxes(trap_labels,
                                                                   trap_props,
                                                                   trapAreaThreshold=trap_area_threshold,
                                                                   trapWidth=trap_align_metadata['trap_width'],
                                                                   trapHeight=trap_align_metadata['trap_height'])

                # create boolean array to contain filtered, correctly-shaped trap bounding boxes
                first_frame_trap_mask = np.zeros(traps.shape)
                for i,bbox in enumerate(trap_bboxes):
                    first_frame_trap_mask[bbox[0]:bbox[2],bbox[1]:bbox[3]] = True

                good_trap_labels = measure.label(first_frame_trap_mask)
                good_trap_props = measure.regionprops(good_trap_labels)

                # widen the traps to merge them into "trap regions" above and below the central trough
                dilated_traps = morphology.dilation(first_frame_trap_mask, dilator)

                if p['debug']:
                    io.imshow(dilated_traps)
                    plt.title('Dilated trap masks')
                    plt.show()

                dilated_trap_labels = measure.label(dilated_traps)
                dilated_trap_props = measure.regionprops(dilated_trap_labels)
                # filter merged trap regions by area
                areas = [reg.area for reg in dilated_trap_props]
                labels = [reg.label for reg in dilated_trap_props]

                for idx,area in enumerate(areas):
                    if area < p['compile']['merged_trap_region_area_threshold']:

                        label = labels[idx]
                        dilated_traps[dilated_trap_labels == label] = 0

                dilated_trap_labels = measure.label(dilated_traps)
                dilated_trap_props = measure.regionprops(dilated_trap_labels)

                if p['debug']:
                    io.imshow(dilated_traps)
                    plt.title("Area-filtered dilated traps")
                    plt.show()

                # get centroids for each "trap region" identified in first frame
                centroids = np.round(np.asarray([reg.centroid for reg in dilated_trap_props]))
                if p['debug']:
                    print(centroids)

                # test whether we could crop a (512,512) square from each "trap region", with the centroids as the centers of the crops, withoug going out-of-bounds
                top_test = centroids[:,0]-256 > 0
                bottom_test = centroids[:,0]+256 < dilated_trap_labels.shape[0]
                test_array = np.stack((top_test,bottom_test))

                # get the index of the first identified "trap region" that we can get our (512,512) crop from, use that centroid for nucleus of cropping a stack of phase images with shape (frame_number,512,512,1) from all images in series
                if p['debug']:
                    print(test_array)
                    print(np.all(test_array,axis=0))

                good_trap_region_index = np.where(np.all(test_array, axis=0))[0][0]
                centroid = centroids[good_trap_region_index,:].astype('uint16')
                if p['debug']:
                    print(centroid)

                # get the (frame_number,512,512,1)-sized stack for image aligment
                align_region_stack = np.zeros((trap_align_metadata['frame_count'],512,512,1), dtype='uint16')

                for frame,fn in enumerate(fov_file_names):
                    imgPath = os.path.join(p['experiment_directory'],p['image_directory'],fn)
                    frame_img = io.imread(imgPath)
                    # detect if there are multiple imaging channels, and rearrange image if necessary, keeping only the phase image
                    frame_img = permute_image(frame_img, trap_align_metadata)
                    align_region_stack[frame,:,:,0] = frame_img[centroid[0]-256:centroid[0]+256,
                                                             centroid[1]-256:centroid[1]+256]

                # if p['debug']:
                #     colNum = 10
                #     fig,ax = plt.subplots(ncols=colNum, figsize=(20,20))

                #     for pltIdx in range(colNum):
                #         ax[pltIdx].imshow(align_region_stack[pltIdx*10,:,:,0])

                #     plt.title('Alignment stack images');
                #     plt.show();

                # run model on all frames
                batch_size=p['compile']['channel_prediction_batch_size']
                information("Predicting trap regions for (512,512) slice through all frames.")

                data_gen_args = {'batch_size':batch_size,
                         'n_channels':1,
                         'normalize_to_one':True,
                         'shuffle':False}
                predict_gen_args = {'verbose':1,
                        'use_multiprocessing':True,
                        'workers':p['num_analyzers']}
                # predict_gen_args = {'verbose':1,
                #         'use_multiprocessing':False}

                img_generator = TrapSegmentationDataGenerator(align_region_stack, **data_gen_args)

                align_region_predictions = model.predict(img_generator, **predict_gen_args)
                #align_region_stack = mm3.apply_median_filter_and_normalize(align_region_stack)
                #align_region_predictions = model.predict(align_region_stack, batch_size=batch_size)
                # reduce dimensionality such that the class predictions are now (frame_number,512,512), and each voxel is labelled as the predicted region, i.e., 0=trap, 1=central trough, 2=background.
                align_region_class_predictions = np.argmax(align_region_predictions, axis=3)

                # if p['debug']:
                #     colNum = 10
                #     fig,ax = plt.subplots(ncols=colNum, figsize=(20,20))

                #     for pltIdx in range(colNum):
                #         ax[pltIdx].imshow(align_region_class_predictions[pltIdx*10,:,:])

                #     plt.title('Alignment stack predictions');
                #     plt.show();

                # get boolean array where trap predictions are True
                align_traps = align_region_class_predictions == 0

                # if p['debug']:
                #     colNum = 10
                #     fig,ax = plt.subplots(ncols=colNum, figsize=(20,20))

                #     for pltIdx in range(colNum):
                #         ax[pltIdx].imshow(align_traps[pltIdx*10,:,:])

                #     plt.title('Alignment trap masks');
                #     plt.show();

                # allocate array to store filtered traps over time
                align_trap_mask_stack = np.zeros(align_traps.shape)
                for frame in range(trap_align_metadata['frame_count']):

                    frame_trap_labels = measure.label(align_traps[frame,:,:])
                    frame_trap_props = measure.regionprops(frame_trap_labels)

                    trap_bboxes = get_frame_trap_bounding_boxes(frame_trap_labels,
                                                                    frame_trap_props,
                                                                    trapAreaThreshold=trap_area_threshold,
                                                                    trapWidth=trap_align_metadata['trap_width'],
                                                                    trapHeight=trap_align_metadata['trap_height'])

                    for i,bbox in enumerate(trap_bboxes):
                        align_trap_mask_stack[frame,bbox[0]:bbox[2],bbox[1]:bbox[3]] = True

                # if p['debug']:
                #     colNum = 10
                #     fig,ax = plt.subplots(ncols=colNum, figsize=(20,20))

                #     for pltIdx in range(colNum):
                #         ax[pltIdx].imshow(align_trap_mask_stack[pltIdx*10,:,:])

                #     plt.title('Filtered alignment trap masks');
                #     plt.show();

                labelled_align_trap_mask_stack = measure.label(align_trap_mask_stack)

                trapTriggered = False
                for frame in range(trap_align_metadata['frame_count']):
                    anyTraps = np.any(labelled_align_trap_mask_stack[frame,:,:] > 0)
                    # if anyTraps is False, that means no traps were detected for this frame. This usuall occurs due to a bug in our imaging system,
                    #    which can cause it to miss the occasional frame. Should be fine to snag labels from prior frame.
                    if not anyTraps:
                        trapTriggered = True
                        information("Frame at index {} has no detected traps. Borrowing labels from an adjacent frame.".format(frame))
                        if frame > 0:
                            labelled_align_trap_mask_stack[frame,:,:] = labelled_align_trap_mask_stack[frame-1,:,:]
                        else:
                            labelled_align_trap_mask_stack[frame,:,:] = labelled_align_trap_mask_stack[frame+1,:,:]

                if trapTriggered:
                    repaired_align_trap_mask_stack = labelled_align_trap_mask_stack > 0
                    labelled_align_trap_mask_stack = measure.label(repaired_align_trap_mask_stack)

                align_trap_props = measure.regionprops(labelled_align_trap_mask_stack)

                areas = np.array([trap.area for trap in align_trap_props])
                labels = [trap.label for trap in align_trap_props]
                good_align_trap_props = []
                bad_align_trap_props = []
                #mode_area = stats.mode(areas)[0]
                expected_area = trap_align_metadata['trap_width'] * trap_align_metadata['trap_height'] * trap_align_metadata['frame_count']

                if p['debug']:
                    pprint(areas)
                    print(expected_area)

                    if not expected_area in areas:
                        print("No trap has expected total area. Saving labelled masks for debugging as labelled_align_trap_mask_stack.tif")
                        io.imsave("labelled_align_trap_mask_stack.tif", labelled_align_trap_mask_stack.astype('uint8'))
                        io.imsave("masks.tif", align_traps.astype('uint8'))
                        # occasionally our microscope misses an image, resulting in no traps for a single frame. This obviously messes up image alignment here....

                for trap in align_trap_props:
                    if trap.area != expected_area:
                        bad_align_trap_props.append(trap.label)
                    else:
                        good_align_trap_props.append(trap)

                for label in bad_align_trap_props:
                    labelled_align_trap_mask_stack[labelled_align_trap_mask_stack == label] = 0

                align_centroids = []
                for frame in range(trap_align_metadata['frame_count']):
                    align_centroids.append([reg.centroid for reg in measure.regionprops(labelled_align_trap_mask_stack[frame,:,:])])

                align_centroids = np.asarray(align_centroids)
                shifts = np.mean(align_centroids - align_centroids[0,:,:], axis=1)
                integer_shifts = np.round(shifts).astype('int16')

                good_trap_bboxes_dict = {}
                for trap in good_trap_props:
                    good_trap_bboxes_dict[trap.label] = trap.bbox

                # pprint(good_trap_bboxes_dict) # uncomment for debugging
                bbox_shift_dict = shift_bounding_boxes(good_trap_bboxes_dict, integer_shifts, img.shape[0])
                # pprint(bbox_shift_dict) # uncomment for debugging

                trap_images_fov_dict, trap_closed_end_px_dict =crop_traps(fov_file_names, good_trap_props, good_trap_labels, bbox_shift_dict, trap_align_metadata)

                for fn in fov_file_names:
                    analyzed_imgs[fn]['channels'] = trap_closed_end_px_dict[fn]

                if p['compile']['do_channel_masks']:
                    fov_channel_masks = make_channel_masks_CNN(bbox_shift_dict)
                    channel_masks[fov_id] = fov_channel_masks
                    # pprint(channel_masks) # uncomment for debugging

                if p['compile']['do_slicing']:

                    if p['output'] == "TIFF":

                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            save_tiffs(trap_images_fov_dict, analyzed_imgs, fov_id)

                    elif p['output'] == "HDF5":
                        # Or write it to hdf5
                        save_hdf5(trap_images_fov_dict, fov_file_names, analyzed_imgs, fov_id, channel_masks)

        # save metadata to a .pkl and a human readable txt file
        information('Saving metadata from analyzed images...')
        with open(os.path.join(p['ana_dir'], 'TIFF_metadata.pkl'), 'wb') as tiff_metadata:
            pickle.dump(analyzed_imgs, tiff_metadata, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(p['ana_dir'], 'TIFF_metadata.txt'), 'w') as tiff_metadata:
            pprint(analyzed_imgs, stream=tiff_metadata)
        information('Saved metadata from analyzed images.')

    ### Make table for jd time to FOV and time point
    if not p['compile']['do_time_table']:
       information('Skipping time table creation.')
    else:
        time_table = make_time_table(analyzed_imgs)

    ### Make consensus channel masks and get other shared metadata #################################
    if not p['compile']['do_channel_masks'] and p['compile']['do_slicing']:
        channel_masks = load_channel_masks()

    elif p['compile']['do_channel_masks']:

        if p['compile']['find_channels_method'] == 'peaks':
            # only calculate channels masks from images before t_end in case it is specified
            if t_start:
                analyzed_imgs = {fn : i_metadata for fn, i_metadata in six.iteritems(analyzed_imgs) if i_metadata['t'] >= t_start}
            if t_end:
                analyzed_imgs = {fn : i_metadata for fn, i_metadata in six.iteritems(analyzed_imgs) if i_metadata['t'] <= t_end}

            # Uses channelinformation from the already processed image data
            channel_masks = make_masks(analyzed_imgs)

        elif p['compile']['find_channels_method'] == 'Unet':

            # save the channel mask dictionary to a pickle and a text file
            with open(os.path.join(p['ana_dir'], 'channel_masks.pkl'), 'wb') as cmask_file:
                pickle.dump(channel_masks, cmask_file, protocol=pickle.HIGHEST_PROTOCOL)
            with open(os.path.join(p['ana_dir'], 'channel_masks.txt'), 'w') as cmask_file:
                pprint(channel_masks, stream=cmask_file)

    ### Slice and write TIFF files into channels ###################################################
    if p['compile']['do_slicing']:

        information("Saving channel slices.")
        if p['compile']['find_channels_method'] == 'peaks':

            # do it by FOV. Not set up for multiprocessing
            for fov, peaks in six.iteritems(channel_masks):

                # skip fov if not in the group
                if user_spec_fovs and fov not in user_spec_fovs:
                    continue

                information("Loading images for FOV %03d." % fov)

                # get filenames just for this fov along with the julian date of acquistion
                send_to_write = [[k, v['t']] for k, v in six.iteritems(analyzed_imgs) if v['fov'] == fov]

                # sort the filenames by jdn
                send_to_write = sorted(send_to_write, key=lambda time: time[1])

                if p['output'] == 'TIFF':
                    #This is for loading the whole raw tiff stack and then slicing through it
                    tiff_stack_slice_and_write(send_to_write, channel_masks, analyzed_imgs)

                elif p['output'] == 'HDF5':
                    # Or write it to hdf5
                    hdf5_stack_slice_and_write(send_to_write, channel_masks, analyzed_imgs)

            information("Channel slices saved.")

def fov_plot_channels(fov_id, crosscorrs, specs, outputdir='.', phase_plane='c1'):
    '''
    Creates a plot with the channels with guesses for empties and full channels.
    The plot is saved in PDF format.

    Parameters
    fov_id : str
        file name of the hdf5 file name in originals
    crosscorrs : dictionary
        dictionary for cross correlation values for all fovs.
    specs: dictionary
        dictionary for channal assignment (Analyze/Don't Analyze/Background).

    '''

    information("Plotting channels for FOV %d." % fov_id)

    # set up figure for user assited choosing
    n_peaks = len(specs[fov_id].keys())
    axw=1
    axh=4*axw
    nrows=3
    ncols=int(n_peaks)
    fig = plt.figure(num='none', facecolor='w',figsize=(ncols*axw,nrows*axh))
    gs = gridspec.GridSpec(nrows,ncols,wspace=0.5,hspace=0.1,top=0.90)

    # plot the peaks peak by peak using sorted list
    sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
    npeaks = len(sorted_peaks)

    for n, peak_id in enumerate(sorted_peaks):
        if crosscorrs:
            peak_xc = crosscorrs[fov_id][peak_id] # get cross corr data from dict

        # load data for figure
        image_data = load_stack(fov_id, peak_id, color=phase_plane)

        first_img = rescale_intensity(image_data[0,:,:]) # phase image at t=0
        last_img = rescale_intensity(image_data[-1,:,:]) # phase image at end

        # append an axis handle to ax list while adding a subplot to the figure which has a
        axhi = fig.add_subplot(gs[0,n])
        axmid = fig.add_subplot(gs[1,n])
        axlo = fig.add_subplot(gs[2,n])

        # plot the first image in each channel in top row
        ax=axhi
        ax.imshow(first_img,cmap=plt.cm.gray, interpolation='nearest')
        ax.axis('off')
        ax.set_title(str(peak_id), fontsize = 12)
        if n == 0:
            ax.set_ylabel("first time point")

        # plot middle row using last time point with highlighting for empty/full
        ax=axmid
        ax.axis('off')
        #ax.imshow(last_img,cmap=plt.cm.gray, interpolation='nearest')
        #H,W = last_img.shape
        #img = np.zeros((H,W,3))
        if specs[fov_id][peak_id] == 1: # 1 means analyze, show green
            #img[:,:,1]=last_img
            cmap=plt.cm.Greens_r
        elif specs[fov_id][peak_id] == 0: # 0 means reference, show blue
            #img[:,:,2]=last_img
            cmap=plt.cm.Blues_r
        else: # otherwise show red, means don't analyze
            #img[:,:,0]=last_img
            cmap=plt.cm.Reds_r
        ax.imshow(last_img,cmap=cmap, interpolation='nearest')

        # format
        if n == 0:
            ax.set_ylabel("last time point")

        # finally plot the cross correlations a cross time
        ax=axlo
        if crosscorrs: # don't try to plot if it's not there.
            ccs = peak_xc['ccs'] # list of cc values
            ax.plot(ccs,range(len(ccs)))
            ax.set_title('avg=%1.2f' % peak_xc['cc_avg'], fontsize = 8)
        else:
            ax.plot(np.zeros(10), range(10))

        ax.get_xaxis().set_ticks([0.8,0.9,1.0])
        ax.set_xlim((0.8,1))
        ax.tick_params('x',labelsize=8)
        if not n == 0:
            ax.set_yticks([])
        else:
            ax.set_ylabel("time index, CC on X")


    fig.suptitle("FOV {:d}".format(fov_id),fontsize=14)
    fileout=os.path.join(outputdir,'fov_xy{:03d}.pdf'.format(fov_id))
    fig.savefig(fileout,bbox_inches='tight',pad_inches=0)
    plt.close('all')
    information("Written FOV {}'s channels in {}".format(fov_id,fileout))

    return specs

def fov_cell_segger_plot_channels(fov_id, predictionDict, specs, outputdir='.', phase_plane='c1'):
    '''
    Creates a plot with the channels with guesses for empties and full channels.
    The plot is saved in PDF format.

    Parameters
    fov_id : str
        file name of the hdf5 file name in originals
    predictionDict : dictionary
        dictionary for cross correlation values for all fovs.
    specs: dictionary
        dictionary for channal assignment (Analyze/Don't Analyze/Background).

    '''

    information("Plotting channels for FOV %d." % fov_id)

    # set up figure for user assited choosing
    n_peaks = len(specs[fov_id].keys())
    axw=1
    axh=4*axw
    nrows=3
    ncols=int(n_peaks)
    fig = plt.figure(num='none', facecolor='w',figsize=(ncols*axw,nrows*axh))
    gs = gridspec.GridSpec(nrows,ncols,wspace=0.5,hspace=0.1,top=0.90)

    # plot the peaks peak by peak using sorted list
    sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
    npeaks = len(sorted_peaks)

    for n, peak_id in enumerate(sorted_peaks):
        if predictionDict:
            predictions = predictionDict[fov_id][peak_id] # get predictions array

        # load data for figure
        image_data = load_stack(fov_id, peak_id, color=phase_plane)

        first_img = rescale_intensity(image_data[0,:,:]) # phase image at t=0
        last_img = rescale_intensity(image_data[-1,:,:]) # phase image at end

        # append an axis handle to ax list while adding a subplot to the figure which has a
        axhi = fig.add_subplot(gs[0,n])
        axmid = fig.add_subplot(gs[1,n])
        axlo = fig.add_subplot(gs[2,n])

        # plot the first image in each channel in top row
        ax=axhi
        ax.imshow(first_img,cmap=plt.cm.gray, interpolation='nearest')
        ax.axis('off')
        ax.set_title(str(peak_id), fontsize = 12)
        if n == 0:
            ax.set_ylabel("first time point")

        # plot middle row using last time point with highlighting for empty/full
        ax=axmid
        ax.axis('off')
        #ax.imshow(last_img,cmap=plt.cm.gray, interpolation='nearest')
        #H,W = last_img.shape
        #img = np.zeros((H,W,3))
        if specs[fov_id][peak_id] == 1: # 1 means analyze, show green
            #img[:,:,1]=last_img
            cmap=plt.cm.Greens_r
        elif specs[fov_id][peak_id] == 0: # 0 means reference, show blue
            #img[:,:,2]=last_img
            cmap=plt.cm.Blues_r
        else: # otherwise show red, means don't analyze
            #img[:,:,0]=last_img
            cmap=plt.cm.Reds_r
        ax.imshow(last_img,cmap=cmap, interpolation='nearest')

        # format
        if n == 0:
            ax.set_ylabel("last time point")

        # finally plot the prediction values as horizontal bar chart
        ax=axlo
        if predictionDict:
            ax.barh(range(len(predictions)), predictions)
            #ax.vlines(x=p['channel_picker']['channel_picking_threshold'], ymin=-1, ymax=5, linestyles='dashed',colors='red')
            ax.set_title('cell count', fontsize = 8)
        else:
            ax.plot(np.zeros(10), range(10))

        # ax.set_xlim((0,1)) # set limits to (0,1)
        #ax.get_xaxis().set_ticks([])
        if not n == 0:
            ax.get_yaxis().set_ticks([])
        else:
            ax.set_yticklabels(labels=["","1","2","3","4","5"])
            ax.set_ylabel("")

    fig.suptitle("FOV {:d}".format(fov_id),fontsize=14)
    fileout=os.path.join(outputdir,'fov_xy{:03d}.pdf'.format(fov_id))
    fig.savefig(fileout,bbox_inches='tight',pad_inches=0)
    plt.close('all')
    information("Written FOV {}'s channels in {}".format(fov_id,fileout))

    return specs

# function which makes the UI plot
def fov_choose_channels_UI(fov_id, crosscorrs, specs, UI_images):
    '''Creates a plot with the channels with guesses for empties and full channels,
    and requires the user to choose which channels to use for analysis and which to
    average for empties and subtraction.

    Parameters
    fov_file : str
        file name of the hdf5 file name in originals
    fov_xcorrs : dictionary
        dictionary for cross correlation values for all fovs.

    Returns
    bgdr_peaks : list
        list of peak id's (int) of channels to be used for subtraction
    spec_file_pkl : pickle file
        saves the lists cell_peaks, bgrd_peaks, and drop_peaks to a pkl file

    '''

    information("Starting channel picking for FOV %d." % fov_id)

    # define functions here so they have access to variables
    # for UI. change specification of channel
    def onclick_cells(event):
        try:
            peak_id = int(event.inaxes.get_title())
        except AttributeError:
            return

        # reset image to be updated based on user clicks
        ax_id = sorted_peaks.index(peak_id) * 3 + 1
        new_img = last_imgs[sorted_peaks.index(peak_id)]
        ax[ax_id].imshow(new_img, cmap=plt.cm.gray, interpolation='nearest')

        # if it says analyze, change to empty
        if specs[fov_id][peak_id] == 1:
            specs[fov_id][peak_id] = 0
            ax[ax_id].imshow(np.dstack((ones_array*0.1, ones_array*0.1, ones_array)), alpha=0.25)
            #mm3.information("peak %d now set to empty." % peak_id)

        # if it says empty, change to don't analyze
        elif specs[fov_id][peak_id] == 0:
            specs[fov_id][peak_id] = -1
            ax[ax_id].imshow(np.dstack((ones_array, ones_array*0.1, ones_array*0.1)), alpha=0.25)
            #mm3.information("peak %d now set to ignore." % peak_id)

        # if it says don't analyze, change to analyze
        elif specs[fov_id][peak_id] == -1:
            specs[fov_id][peak_id] = 1
            ax[ax_id].imshow(np.dstack((ones_array*0.1, ones_array, ones_array*0.1)), alpha=0.25)
            #mm3.information("peak %d now set to analyze." % peak_id)

        plt.draw()
        return

    # set up figure for user assited choosing
    n_peaks = len(specs[fov_id].keys())
    fig = plt.figure(figsize=(int(n_peaks/2), 12))
    fig.set_size_inches(int(n_peaks/2),12)
    ax = [] # for axis handles

    # plot the peaks peak by peak using sorted list
    sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
    npeaks = len(sorted_peaks)
    last_imgs = [] # list that holds last images for updating figure

    for n, peak_id in enumerate(sorted_peaks, start=1):
        if crosscorrs:
            peak_xc = crosscorrs[fov_id][peak_id] # get cross corr data from dict

        # load data for figure
        # image_data = mm3.load_stack(fov_id, peak_id, color='c1')

        # first_img = rescale_intensity(image_data[0,:,:]) # phase image at t=0
        # last_img = rescale_intensity(image_data[-1,:,:]) # phase image at end
        last_imgs.append(UI_images[fov_id][peak_id]['last']) # append for updating later
        # del image_data # clear memory (maybe)

        # append an axis handle to ax list while adding a subplot to the figure which has a
        # column for each peak and 3 rows

        # plot the first image in each channel in top row
        ax.append(fig.add_subplot(3, npeaks, n))
        ax[-1].imshow(UI_images[fov_id][peak_id]['first'],
                      cmap=plt.cm.gray, interpolation='nearest')
        ax = format_channel_plot(ax, peak_id) # format axis and title
        if n == 1:
            ax[-1].set_ylabel("first time point")

        # plot middle row using last time point with highlighting for empty/full
        ax.append(fig.add_subplot(3, npeaks, n + npeaks))
        ax[-1].imshow(UI_images[fov_id][peak_id]['last'],
                      cmap=plt.cm.gray, interpolation='nearest')

        # color image based on if it is thought empty or full
        ones_array = np.ones_like(UI_images[fov_id][peak_id]['last'])
        if specs[fov_id][peak_id] == 1: # 1 means analyze, show green
            ax[-1].imshow(np.dstack((ones_array*0.1, ones_array, ones_array*0.1)), alpha=0.25)
        else: # otherwise show red, means don't analyze
            ax[-1].imshow(np.dstack((ones_array, ones_array*0.1, ones_array*0.1)), alpha=0.25)

        # format
        ax = format_channel_plot(ax, peak_id)
        if n == 1:
            ax[-1].set_ylabel("last time point")

        # finally plot the cross correlations a cross time
        ax.append(fig.add_subplot(3, npeaks, n + 2*npeaks))
        if crosscorrs: # don't try to plot if it's not there.
            ccs = peak_xc['ccs'] # list of cc values
            ax[-1].plot(ccs, range(len(ccs)))
            ax[-1].set_title('avg=%1.2f' % peak_xc['cc_avg'], fontsize = 8)
        else:
            pass
            # ax[-1].plot(np.zeros(10), range(10))

        ax[-1].set_xlim((0.8,1))
        ax[-1].get_xaxis().set_ticks([])
        if not n == 1:
            ax[-1].get_yaxis().set_ticks([])
        else:
            ax[-1].set_ylabel("time index, CC on X")

    # show the plot finally
    fig.suptitle("FOV %d" % fov_id)

    # enter user input
    # ask the user to correct cell/nocell calls
    cells_handler = fig.canvas.mpl_connect('button_press_event', onclick_cells)
    #plt.close() resolves the event loop running error but the window stops getting displayed
    #https://stackoverflow.com/questions/27280777/pylab-show-qcoreapplicationexec-the-event-loop-is-already-running

    # matplotlib has difefrent behavior for interactions in different versions.
    if float(mpl.__version__[:3]) < 1.5: # check for verions less than 1.5
        plt.show(block=False)
        raw_input("Click colored channels to toggle between analyze (green), use for empty (blue), and ignore (red).\nPrees enter to go to the next FOV.")
    else:
        print("Click colored channels to toggle between analyze (green), use for empty (blue), and ignore (red).\nClose figure to go to the next FOV.")
        plt.show(block=True)
    fig.canvas.mpl_disconnect(cells_handler)
    plt.close()

    return specs

def fov_choose_channels_UI_II(fov_id, crosscorrs, specs, UI_images, params):

    if not os.path.isdir(params['channel_select_dir']):
        os.mkdir(params['channel_select_dir'])

    n_peaks = len(specs[fov_id].keys())
    fig = plt.figure(figsize=(int(n_peaks/2), 9))
    fig.set_size_inches(int(n_peaks/2),9)
    ax=[]
    # plot the peaks peak by peak using sorted list
    sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
    npeaks = len(sorted_peaks)
    last_imgs = [] # list that holds last images for updating figure

    for n, peak_id in enumerate(sorted_peaks, start=1):
        if crosscorrs:
            peak_xc = crosscorrs[fov_id][peak_id] # get cross corr data from dict

        # load data for figure
        # image_data = mm3.load_stack(fov_id, peak_id, color='c1')

        # first_img = rescale_intensity(image_data[0,:,:]) # phase image at t=0
        # last_img = rescale_intensity(image_data[-1,:,:]) # phase image at end
        last_imgs.append(UI_images[fov_id][peak_id]['last']) # append for updating later
        # del image_data # clear memory (maybe)

        # append an axis handle to ax list while adding a subplot to the figure which has a
        # column for each peak and 3 rows

        # plot the first image in each channel in top row
        ax.append(fig.add_subplot(3, npeaks, n))
        ax[-1].imshow(UI_images[fov_id][peak_id]['first'],
                      cmap=plt.cm.gray, interpolation='nearest')
        ax = format_channel_plot(ax, peak_id) # format axis and title
        if n == 1:
            ax[-1].set_ylabel("first time point")

        # plot middle row using last time point with highlighting for empty/full
        ax.append(fig.add_subplot(3, npeaks, n + npeaks))
        ax[-1].imshow(UI_images[fov_id][peak_id]['last'],
                      cmap=plt.cm.gray, interpolation='nearest')

        # # color image based on if it is thought empty or full
        # ones_array = np.ones_like(UI_images[fov_id][peak_id]['last'])
        # if specs[fov_id][peak_id] == 1: # 1 means analyze, show green
        #     ax[-1].imshow(np.dstack((ones_array*0.1, ones_array, ones_array*0.1)), alpha=0.25)
        # else: # otherwise show red, means don't analyze
        #     ax[-1].imshow(np.dstack((ones_array, ones_array*0.1, ones_array*0.1)), alpha=0.25)

        # format
        ax = format_channel_plot(ax, peak_id)
        if n == 1:
            ax[-1].set_ylabel("last time point")

        # finally plot the cross correlations a cross time
        ax.append(fig.add_subplot(3, npeaks, n + 2*npeaks))
        if crosscorrs: # don't try to plot if it's not there.
            ccs = peak_xc['ccs'] # list of cc values
            ax[-1].plot(ccs, range(len(ccs)))
            ax[-1].set_title('avg=%1.2f' % peak_xc['cc_avg'], fontsize = 8)
        else:
            pass
            # ax[-1].plot(np.zeros(10), range(10))

        ax[-1].set_xlim((0.8,1))
        ax[-1].get_xaxis().set_ticks([])
        if not n == 1:
            ax[-1].get_yaxis().set_ticks([])
        else:
            ax[-1].set_ylabel("time index, CC on X")

    # show the plot finally
    fig.suptitle("FOV %d" % fov_id)
    plt.tight_layout(pad=0.2)
    plt.savefig(os.path.join(params['channel_select_dir'],f"{fov_id}.png"))
    plt.close()

    information("Starting channel picking for FOV %d." % fov_id)
    im=image.imread(os.path.join(params['channel_select_dir'],f"{fov_id}.png"))

    tot=np.array(im)
    napari.current_viewer().add_image(tot, name="Fov"+str(fov_id)+"_img", visible=False)
    napari.current_viewer().add_points([], name="Fov"+str(fov_id)+"_pts", visible=False, face_color='r', size=20)
    offset=43
    # Add points in the points layer according to the cross-correlation value
    namei="Fov"+str(fov_id)+"_img"
    namep="Fov"+str(fov_id)+"_pts"
    (max_height,max_width,_)=napari.current_viewer().layers[namei].data_raw.shape
    width_per_peak=(max_width-offset)//npeaks

    for i,peak_id in enumerate(sorted_peaks):

        # Estimate the coordinates from peak id's index
        x= offset+ width_per_peak*i + (4*width_per_peak)//5

        # Adding the points at 1/3 height from bottom
        # should be good enough for visualization
        y1= (max_height)//2
        y2= (max_height)//2 + 50

        if specs[fov_id][peak_id]==0:
            # empty(0) => 1 points in the peak partition
            napari.current_viewer().layers[namep].add((y1, x))
        elif specs[fov_id][peak_id]==-1:
            # ignore(-1) => 2 points in the peak partition
            napari.current_viewer().layers[namep].add((y1, x))
            napari.current_viewer().layers[namep].add((y2, x))

    return specs

def channelProcessor(params):
    ana_dir = params['ana_dir']
    specs = yaml.safe_load(Path(ana_dir+'specs.yaml').read_text())

    if params['FOV']:
        if '-' in params['FOV']:
            user_spec_fovs = range(int(params['FOV'].split("-")[0]),
                                   int(params['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in params['FOV'].split(",")]
    else:
        user_spec_fovs = []

    # load channel masks
    channel_masks = load_channel_masks()

    # make list of FOVs to process (keys of channel_mask file), but only if there are channels
    fov_id_list = sorted([fov_id for fov_id, peaks in six.iteritems(channel_masks) if peaks])

    # remove fovs if the user specified so
    if (len(user_spec_fovs) > 0):
        fov_id_list = [int(fov) for fov in fov_id_list if fov in user_spec_fovs]

    # Mark all as analyze intitially
    # The points layer analysis will take care later on
    for fov_id in fov_id_list:
        sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
        for peak_id in sorted_peaks:
            specs[fov_id][peak_id]=1

    for fov_id in fov_id_list:
        sorted_peaks = sorted([peak_id for peak_id in specs[fov_id].keys()])
        npeaks = len(sorted_peaks)

        namei="Fov"+str(fov_id)+"_img"
        namep="Fov"+str(fov_id)+"_pts"
        (_,max_width,_)=napari.current_viewer().layers[namei].data_raw.shape
        pts=napari.current_viewer().layers[namep]._view_data
        # print(f'{fov_id}', pts)
        offset=43
        width_per_peak=(max_width-offset)//npeaks

        # analyze(1) => 0 points in the peak partition
        # empty(0) => 1 points in the peak partition
        # ignore(-1) => 2 points in the peak partititon

        for pt in pts:
            peak_id=sorted_peaks[max(0,min(int((pt[1]-offset)//width_per_peak), len(sorted_peaks)-1))]
            specs[fov_id][peak_id]-=1

    # Save out specs file in yaml format

    with open(os.path.join(ana_dir, 'specs.yaml'), 'w') as specs_file:
        yaml.dump(data=specs, stream=specs_file, default_flow_style=False, tags=None)
    # print(specs)
    print("Channel Picking Completed")

# function for better formatting of channel plot
def format_channel_plot(ax, peak_id):
    '''Removes axis and puts peak as title from plot for channels'''
    ax[-1].get_xaxis().set_ticks([])
    ax[-1].get_yaxis().set_ticks([])
    ax[-1].set_title(str(peak_id), fontsize = 8)
    return ax

# function to preload all images for all FOVs, hopefully saving time
def preload_images(specs, fov_id_list):
    '''This dictionary holds the first and last image
    for all channels in all FOVS. It is passed to the UI so that the
    figures can be populated much faster
    '''

    # Intialized the dicionary
    UI_images = {}

    for fov_id in fov_id_list:
        information("Preloading images for FOV {}.".format(fov_id))
        UI_images[fov_id] = {}
        for peak_id in specs[fov_id].keys():
            image_data = load_stack(fov_id, peak_id, color=params['phase_plane'])
            UI_images[fov_id][peak_id] = {'first' : None, 'last' : None} # init dictionary
             # phase image at t=0. Rescale intenstiy and also cut the size in half
            first_image = params['channel_picker']['first_image']
            UI_images[fov_id][peak_id]['first'] = image_data[first_image,::2,::2]
            last_image = params['channel_picker']['last_image']
            # phase image at end
            UI_images[fov_id][peak_id]['last'] = image_data[last_image,::2,::2]

    return UI_images

### For when this script is run from the terminal ##################################
def channelPicker(params):
    '''mm3_ChannelPicker.py allows the user to identify full and empty channels.'''

    # Load the project parameters file
    information('Loading experiment parameters.')
    p = params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    specfile = None

    # set cross correlation calculation flag
    # if namespace.saved_cross_correlations:
    #     do_crosscorrs = False
    # else:
    do_crosscorrs = p['channel_picker']['do_crosscorrs']

    do_CNN = p['channel_picker']['do_CNN']
    do_seg = p['channel_picker']['do_seg']

    # set interactive flag
    # if namespace.noninteractive:
    #     interactive = False
    # else:
    interactive = p['channel_picker']['interactive']

    # assign shorthand directory names
    ana_dir = os.path.join(p['experiment_directory'], p['analysis_directory'])
    chnl_dir = os.path.join(p['experiment_directory'], p['analysis_directory'], 'channels')
    hdf5_dir = os.path.join(p['experiment_directory'], p['analysis_directory'], 'hdf5')

    # load channel masks
    channel_masks = load_channel_masks()

    # make list of FOVs to process (keys of channel_mask file), but only if there are channels
    fov_id_list = sorted([fov_id for fov_id, peaks in six.iteritems(channel_masks) if peaks])

    # remove fovs if the user specified so
    if (len(user_spec_fovs) > 0):
        fov_id_list = [int(fov) for fov in fov_id_list if fov in user_spec_fovs]

    information("Found %d FOVs to process." % len(fov_id_list))

    ### Cross correlations ########################################################################
    if do_CNN:
        # a nested dict to hold predictions per channel per fov.
        crosscorrs = None
        predictionDict = {}

        information('Loading model ....')

        # read in model for inference of empty vs good traps
        model_file_path = p['channel_picker']['channel_picker_model_file']
        model = models.load_model(model_file_path)

        information("Model loaded.")

        for fov_id in fov_id_list:

            predictionDict[fov_id] = {}

            information('Inferring good, empty, and defective traps on fov_id {} using CNN.'.format(fov_id))

            # get list of tiff file names
            tiff_file_names = glob.glob(os.path.join(chnl_dir, "*xy{:0=3}*_c1.tif".format(fov_id)))
            tiff_file_names.sort()
            #print(len(tiff_file_names)) # uncomment for debugging

            # parameters to pass to custom image generator class, TrapKymographPredictionDataGenerator
            cnn_params = {'dim': (210,256),
                      'batch_size': 40,
                      'n_classes': 4,
                      'n_channels': 1,
                      'shuffle': False}
            # set up the image data generator
            channel_image_generator = TrapKymographPredictionDataGenerator(tiff_file_names, **cnn_params)

            # run the model
            predictions = model.predict(channel_image_generator)
            #print(predictions.shape)
            predictions = predictions[:len(tiff_file_names),:]
            #print(predictions.shape)

            # assign each prediction to the proper fov_id, peak_id in predictions dict
            for i,peak_id in enumerate(sorted(channel_masks[fov_id].keys())):
                # put prediction array into dictionary
                #print(i, peak_id) # uncomment for debugging
                predictionDict[fov_id][peak_id] = predictions[i,:]

        # write predictions to pickle and text
        information("Writing channel picking predictions file.")
        with open(os.path.join(ana_dir,"channel_picker_CNN_results.pkl"), 'wb') as preds_file:
            pickle.dump(predictionDict, preds_file, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(ana_dir,"channel_picker_CNN_results.txt"), 'w') as preds_file:
            pprint(predictionDict, stream=preds_file)
        information("Wrote channel picking predictions files.")

    elif do_seg:
        # a nested dict to hold predictions per channel per fov.
        crosscorrs = None
        predictionDict = {}

        information('Loading model ....')

        # read in model for inference of empty vs good traps
        model_file_path = p['segment']['model_file']
        model = models.load_model(model_file_path,
                                  custom_objects={'bce_dice_loss': bce_dice_loss,
                                                  'dice_loss': dice_loss})
        unet_shape = (p['segment']['trained_model_image_height'],
                      p['segment']['trained_model_image_width'])

        cellClassThreshold = p['segment']['cell_class_threshold']
        if cellClassThreshold == 'None': # yaml imports None as a string
            cellClassThreshold = False
        min_object_size = p['segment']['min_object_size']

        information("Model loaded.")

        # arguments to data generator
        data_gen_args = {'batch_size':p['segment']['batch_size'],
                         'n_channels':1,
                         'normalize_to_one':True,
                         'shuffle':False}
        # arguments to predict_generator
        predict_args = dict(use_multiprocessing=True,
                            workers=p['num_analyzers'],
                            verbose=1)

        # predict_args = dict(use_multiprocessing=False,
        #                     verbose=1)

        for fov_id in fov_id_list:

            predictionDict[fov_id] = {}

            information('Inferring number of cells in five evenly spaced frames for each trap in fov {}.'.format(fov_id))

            # assign each prediction to the proper fov_id, peak_id in predictions dict
            counter = 0
            peak_number = len(channel_masks[fov_id])
            for i,peak_id in enumerate(sorted(channel_masks[fov_id].keys())):
                # get list of tiff file names
                tiff_file_name = glob.glob(os.path.join(chnl_dir, "*xy{:0=3}_p{:0=4}_c1.tif".format(fov_id, peak_id)))[0]

                img_array = io.imread(tiff_file_name)
                img_height = img_array.shape[1]
                img_width = img_array.shape[2]
                slice_increment = int(img_array.shape[0]/5)

                # set up stack for images from all peaks
                # this is a bit more complicated than just doing 5 images at a time, but it is much faster
                #   because you don't have nearly as many data transfer steps
                if i == 0:
                    img_stack = np.zeros((5*peak_number,img_height,img_width),dtype='uint16')

                # grab 5 images to load and run cell segmentation
                for j in range(5):
                    img_stack[counter,...] = img_array[slice_increment*j,...]
                    counter += 1

            pad_dict = get_pad_distances(unet_shape, img_height, img_width)

            # pad image to correct size
            if p['debug']:
                print("Padding dictionary:", pad_dict)

            img_stack = np.pad(img_stack,
                               ((0,0),
                               (pad_dict['top_pad'],pad_dict['bottom_pad']),
                               (pad_dict['left_pad'],pad_dict['right_pad'])),
                               mode='constant')
            img_stack = np.expand_dims(img_stack, -1)

            # set up image generator
            image_generator = CellSegmentationDataGenerator(img_stack, **data_gen_args)
            # run predictions
            predictions = model.predict(image_generator, **predict_args)[:,:,:,0]
            if p['debug']:
                fig,ax = plt.subplots(ncols=5)
                for i in range(5):
                    ax[i].imshow(predictions[i,:,:])
                plt.show()

            # binarized and label (if there is a threshold value, otherwise, save a grayscale for debug)
            if cellClassThreshold:
                predictions[predictions >= cellClassThreshold] = 1
                predictions[predictions < cellClassThreshold] = 0
                predictions = predictions.astype('uint8')

                segmented_imgs = np.zeros(predictions.shape, dtype='uint8')
                # process and label each frame of the channel
                for frame in range(segmented_imgs.shape[0]):
                    # get rid of small holes
                    predictions[frame,:,:] = morphology.remove_small_holes(predictions[frame,:,:], min_object_size)
                    # get rid of small objects.
                    predictions[frame,:,:] = morphology.remove_small_objects(morphology.label(predictions[frame,:,:], connectivity=1), min_size=min_object_size)
                    # remove labels which touch the boarder
                    predictions[frame,:,:] = segmentation.clear_border(predictions[frame,:,:])
                    # relabel now
                    segmented_imgs[frame,:,:] = morphology.label(predictions[frame,:,:], connectivity=1)

            else: # in this case you just want to scale the 0 to 1 float image to 0 to 255
                information('Converting predictions to grayscale.')
                segmented_imgs = np.around(predictions * 100)

            # put number of cells detected into array for predictionDict
            counter = 0
            for i,peak_id in enumerate(sorted(channel_masks[fov_id].keys())):

                cell_count_array = np.zeros(5, dtype='uint8')
                for j in range(5):
                    cell_count_array[j] = int(np.max(segmented_imgs[counter,:,:]))
                    counter += 1

                predictionDict[fov_id][peak_id] = cell_count_array

        # write predictions to pickle and text
        information("Writing channel picking predictions file.")
        with open(os.path.join(ana_dir,"channel_picker_seg_results.pkl"), 'wb') as preds_file:
            pickle.dump(predictionDict, preds_file, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(ana_dir,"channel_picker_seg_results.txt"), 'w') as preds_file:
            pprint(predictionDict, stream=preds_file)
        information("Wrote channel picking predictions files.")

    elif do_crosscorrs:
        # a nested dict to hold cross corrs per channel per fov.
        crosscorrs = {}

        # for each fov find cross correlations (sending to pull)
        for fov_id in fov_id_list:
            information("Calculating cross correlations for FOV %d." % fov_id)

            # nested dict keys are peak_ids and values are cross correlations
            crosscorrs[fov_id] = {}

            # initialize pool for analyzing image metadata
            pool = Pool(p['num_analyzers'])

            # find all peak ids in the current FOV
            for peak_id in sorted(channel_masks[fov_id].keys()):
                information("Calculating cross correlations for peak %d." % peak_id)

                # linear loop
                # crosscorrs[fov_id][peak_id] = channel_xcorr(fov_id, peak_id)

                # multiprocessing verion
                crosscorrs[fov_id][peak_id] = pool.apply_async(channel_xcorr, args=(fov_id, peak_id,))

            information('Waiting for cross correlation pool to finish for FOV %d.' % fov_id)

            pool.close() # tells the process nothing more will be added.
            pool.join() # blocks script until everything has been processed and workers exit

            information("Finished cross correlations for FOV %d." % fov_id)

        # get results from the pool and put the results in the dictionary if succesful
        for fov_id, peaks in six.iteritems(crosscorrs):
            for peak_id, result in six.iteritems(peaks):
                if result.successful():
                    # put the results, with the average, and a guess if the channel
                    # is full into the dictionary
                    crosscorrs[fov_id][peak_id] = {'ccs' : result.get(),
                                                   'cc_avg' : np.average(result.get()),
                                                   'full' : np.average(result.get()) < p['channel_picker']['channel_picking_threshold']}
                else:
                    crosscorrs[fov_id][peak_id] = False # put a false there if it's bad

        # linear loop for debug
        # get results from the pool and put the results in the dictionary if succesful
        # for fov_id, peaks in six.iteritems(crosscorrs):
        #     for peak_id, result in six.iteritems(peaks):
        #         crosscorrs[fov_id][peak_id] = {'ccs' : result,
        #                                            'cc_avg' : np.average(result),
        #                                            'full' : np.average(result) < p['channel_picker']['channel_picking_threshold']}

        # write cross-correlations to pickle and text
        information("Writing cross correlations file.")
        with open(os.path.join(ana_dir,"crosscorrs.pkl"), 'wb') as xcorrs_file:
            pickle.dump(crosscorrs, xcorrs_file, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(ana_dir,"crosscorrs.txt"), 'w') as xcorrs_file:
            pprint(crosscorrs, stream=xcorrs_file)
        information("Wrote cross correlations files.")

    # try to load previously calculated cross correlations
    else:
        information('Loading precalculated cross-correlations.')
        try:
            with open(os.path.join(ana_dir,'crosscorrs.pkl'), 'rb') as xcorrs_file:
                crosscorrs = pickle.load(xcorrs_file)
        except:
            crosscorrs = None
            information('Could not load cross-correlations.')

    ### User selection (channel picking) #####################################################
    if specfile == None:
        information('Initializing specifications file.')
        # nested dictionary of {fov : {peak : spec ...}) for if channel should
        # be analyzed, used for empty, or ignored.
        specs = {}

        # if there is cross corrs, use it. Otherwise, just make everything -1
        if crosscorrs:
            # update dictionary on initial guess from cross correlations
            for fov_id, peaks in six.iteritems(crosscorrs):
                specs[fov_id] = {}
                for peak_id, xcorrs in six.iteritems(peaks):
                    # update the guess incase the parameters file was changed
                    xcorrs['full'] = xcorrs['cc_avg'] < p['channel_picker']['channel_picking_threshold']

                    if xcorrs['full'] == True:
                        specs[fov_id][peak_id] = 1
                    else: # default to don't analyze
                        specs[fov_id][peak_id] = -1
        elif do_CNN:

            # update dictionary with inference from CNN

            for fov_id, peakPredictionsDict in six.iteritems(predictionDict):
                fov_id = int(fov_id)
                specs[fov_id] = {}
                for peak_id, predictions in six.iteritems(peakPredictionsDict):

                    if predictions[0] > p['channel_picker']['channel_picking_threshold']:
                        specs[fov_id][peak_id] = 1
                    else:
                        specs[fov_id][peak_id] = -1

            #pprint(specs) # uncomment for debugging

        elif do_seg:

            # update dictionary with inference from cell segmentation based decision

            for fov_id, peakPredictionsDict in six.iteritems(predictionDict):
                fov_id = int(fov_id)
                specs[fov_id] = {}
                for peak_id, predictions in six.iteritems(peakPredictionsDict):

                    # if there was at least one cell in any of the frames checked, keep the trap
                    if np.max(predictions) > 0:
                        specs[fov_id][peak_id] = 1
                    else:
                        specs[fov_id][peak_id] = -1

        else: # just set everything to 1 and go forward.
            for fov_id, peaks in six.iteritems(channel_masks):
                specs[fov_id] = {peak_id: -1 for peak_id in peaks.keys()}

    else:
        information('Loading supplied specifiication file.')
        with open(specfile, 'r') as fin:
            specs = yaml.load(fin)

    if interactive:
        # preload the images
        information('Preloading images.')
        UI_images = preload_images(specs, fov_id_list)

        information('Starting channel picking.')
        # go through the fovs again, same as above
        for fov_id in fov_id_list:

            if do_CNN:
                specs = fov_CNN_choose_channels_UI(fov_id, predictionDict, specs, UI_images)
            elif do_seg:
                specs = fov_cell_segger_choose_channels_UI(fov_id, predictionDict, specs, UI_images)
            else: # crosscorrs == None will default to just picking with no help.
                #specs = fov_choose_channels_UI(fov_id, crosscorrs, specs, UI_images)
                specs=fov_choose_channels_UI_II(fov_id, crosscorrs, specs, UI_images, params)

    else:
        outputdir = os.path.join(ana_dir, "fovs")
        if not os.path.isdir(outputdir):
            os.makedirs(outputdir)
        for fov_id in fov_id_list:
            if crosscorrs:
                specs = fov_plot_channels(fov_id, crosscorrs, specs,
                                          outputdir=outputdir, phase_plane=p['phase_plane'])
            elif do_CNN:
                specs = fov_CNN_plot_channels(fov_id, predictionDict, specs,
                                              outputdir=outputdir, phase_plane=p['phase_plane'])
            elif do_seg:
                specs = fov_cell_segger_plot_channels(fov_id, predictionDict, specs,
                                              outputdir=outputdir, phase_plane=p['phase_plane'])

    # Save out specs file in yaml format
    with open(os.path.join(ana_dir, 'specs.yaml'), 'w') as specs_file:
        yaml.dump(data=specs, stream=specs_file, default_flow_style=False, tags=None)

    information('Finished.')

def subtract(params):
    '''mm3_Subtract.py averages empty channels and then subtractions them from channels with cells'''

    # Load the project parameters file
    information('Loading experiment parameters.')
    #p = mm3_.init_mm3_helpers() # initialized the helper library
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    information('Using {} threads for multiprocessing.'.format(p['num_analyzers']))

    sub_plane = 'c1'

    # Create folders for subtracted info if they don't exist
    if p['output'] == 'TIFF':
        if not os.path.exists(p['empty_dir']):
            os.makedirs(p['empty_dir'])
        if not os.path.exists(p['sub_dir']):
            os.makedirs(p['sub_dir'])

    # load specs file
    specs = load_specs()

    # make list of FOVs to process (keys of specs file)
    fov_id_list = sorted([fov_id for fov_id in specs.keys()])

    # remove fovs if the user specified so
    if user_spec_fovs:
        fov_id_list[:] = [fov for fov in fov_id_list if fov in user_spec_fovs]

    information("Found %d FOVs to process." % len(fov_id_list))

    # determine if we are doing fluorescence or phase subtraction, and set flags
    if sub_plane == p['phase_plane']:
        align = True # used when averaging empties
        sub_method = 'phase' # used in subtract_fov_stack
    else:
        align = False
        sub_method = 'fluor'

    ### Make average empty channels ###############################################################
    if not p['subtract']['do_empties']:
        information("Loading precalculated empties.")
        pass # just skip this part and go to subtraction

    else:
        information("Calculating averaged empties for channel {}.".format(sub_plane))

        need_empty = [] # list holds fov_ids of fov's that did not have empties
        for fov_id in fov_id_list:
            # send to function which will create empty stack for each fov.
            averaging_result = average_empties_stack(fov_id, specs,
                                                         color=sub_plane, align=align)
            # add to list for FOVs that need to be given empties from other FOvs
            if not averaging_result:
                need_empty.append(fov_id)

        # deal with those problem FOVs without empties
        have_empty = list(set(fov_id_list).difference(set(need_empty))) # fovs with empties
        for fov_id in need_empty:
            from_fov = min(have_empty, key=lambda x: abs(x-fov_id)) # find closest FOV with an empty
            copy_result = copy_empty_stack(from_fov, fov_id, color=sub_plane)

    ### Subtract ##################################################################################
    if p['subtract']['do_subtraction']:
        information("Subtracting channels for channel {}.".format(sub_plane))
        for fov_id in fov_id_list:
            # send to function which will create empty stack for each fov.
            subtraction_result = subtract_fov_stack(fov_id, specs,
                                                        color=sub_plane, method=sub_method)
        information("Finished subtraction.")

    # Else just end, they only wanted to do empty averaging.
    else:
        information("Skipping subtraction.")
        pass

def segmentUNet(params):

    information('Loading experiment parameters.')
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    information('Using {} threads for multiprocessing.'.format(p['num_analyzers']))

    # create segmenteation and cell data folder if they don't exist
    if not os.path.exists(p['seg_dir']) and p['output'] == 'TIFF':
        os.makedirs(p['seg_dir'])
    if not os.path.exists(p['cell_dir']):
        os.makedirs(p['cell_dir'])

    # set segmentation image name for saving and loading segmented images
    p['seg_img'] = 'seg_unet'
    p['pred_img'] = 'pred_unet'

    # load specs file
    specs = load_specs()
    # print(specs) # for debugging

    # make list of FOVs to process (keys of channel_mask file)
    fov_id_list = sorted([fov_id for fov_id in specs.keys()])

    # remove fovs if the user specified so
    if user_spec_fovs:
        fov_id_list[:] = [fov for fov in fov_id_list if fov in user_spec_fovs]

    information("Processing %d FOVs." % len(fov_id_list))

    ### Do Segmentation by FOV and then peak #######################################################
    information("Segmenting channels using U-net.")

    # load model to pass to algorithm
    information("Loading model...")

    # if namespace.modelfile:
    #     model_file_path = namespace.modelfile
    # else:
    model_file_path = p['segment']['model_file']
    # *** Need parameter for weights
    seg_model = models.load_model(model_file_path,
                              custom_objects={'bce_dice_loss': bce_dice_loss,
                                              'dice_loss': dice_loss})
    information("Model loaded.")

    for fov_id in fov_id_list:
        segment_fov_unet(fov_id, specs, seg_model, color=p['phase_plane'])

    del seg_model
    information("Finished segmentation.")

def segmentOTSU(params):

    information('Loading experiment parameters.')
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    # create segmenteation and cell data folder if they don't exist
    if not os.path.exists(p['seg_dir']) and p['output'] == 'TIFF':
        os.makedirs(p['seg_dir'])
    if not os.path.exists(p['cell_dir']):
        os.makedirs(p['cell_dir'])

    # set segmentation image name for saving and loading segmented images
    p['seg_img'] = 'seg_otsu'

    # load specs file
    specs = load_specs()

    # make list of FOVs to process (keys of channel_mask file)
    fov_id_list = sorted([fov_id for fov_id in specs.keys()])

    # remove fovs if the user specified so
    if user_spec_fovs:
        fov_id_list[:] = [fov for fov in fov_id_list if fov in user_spec_fovs]

    information("Segmenting %d FOVs." % len(fov_id_list))

    ### Do Segmentation by FOV and then peak #######################################################
    information("Segmenting channels using Otsu method.")

    for fov_id in fov_id_list:
        # determine which peaks are to be analyzed (those which have been subtracted)
        ana_peak_ids = []
        for peak_id, spec in six.iteritems(specs[fov_id]):
            if spec == 1: # 0 means it should be used for empty, -1 is ignore, 1 is analyzed
                ana_peak_ids.append(peak_id)
        ana_peak_ids = sorted(ana_peak_ids) # sort for repeatability

        for peak_id in ana_peak_ids:
            # send to segmentation
            segment_chnl_stack(fov_id, peak_id)

    information("Finished segmentation.")

def Track(params):

    # Load the project parameters file
    information('Loading experiment parameters.')
    p=params

    if p['FOV']:
        if '-' in p['FOV']:
            user_spec_fovs = range(int(p['FOV'].split("-")[0]),
                                   int(p['FOV'].split("-")[1])+1)
        else:
            user_spec_fovs = [int(val) for val in p['FOV'].split(",")]
    else:
        user_spec_fovs = []

    information('Using {} threads for multiprocessing.'.format(p['num_analyzers']))

    information("Using {} images for tracking.".format(p['track']['seg_img']))

    # create segmenteation and cell data folder if they don't exist
    if not os.path.exists(p['seg_dir']) and p['output'] == 'TIFF':
        os.makedirs(p['seg_dir'])
    if not os.path.exists(p['cell_dir']):
        os.makedirs(p['cell_dir'])

    # load specs file
    specs = load_specs()

    # make list of FOVs to process (keys of channel_mask file)
    fov_id_list = sorted([fov_id for fov_id in specs.keys()])

    # remove fovs if the user specified so
    if user_spec_fovs:
        fov_id_list[:] = [fov for fov in fov_id_list if fov in user_spec_fovs]

    ### Create cell lineages from segmented images
    information("Creating cell lineages using standard algorithm.")

    # Load time table, which goes into params
    load_time_table()

    # This dictionary holds information for all cells
    Cells = {}

    # do lineage creation per fov, so pooling can be done by peak
    for fov_id in fov_id_list:
        # update will add the output from make_lineages_function, which is a
        # dict of Cell entries, into Cells
        Cells.update(make_lineages_fov(fov_id, specs))

    information("Finished lineage creation.")

    ### Now prune and save the data.
    information("Curating and saving cell data.")

    # this returns only cells with a parent and daughters
    Complete_Cells = find_complete_cells(Cells)

    ### save the cell data. Use the script mm3_OutputData for additional outputs.
    # All cell data (includes incomplete cells)
    with open(p['cell_dir'] + '/all_cells.pkl', 'wb') as cell_file:
        pickle.dump(Cells, cell_file, protocol=pickle.HIGHEST_PROTOCOL)

    # Just the complete cells, those with mother and daugther
    # This is a dictionary of cell objects.
    with open(os.path.join(p['cell_dir'],'complete_cells.pkl'), 'wb') as cell_file:
        pickle.dump(Complete_Cells, cell_file, protocol=pickle.HIGHEST_PROTOCOL)

    information("Finished curating and saving cell data.")

def find_cells_of_fov_and_peak(Cells, fov_id, peak_id):
    '''Return only cells from a specific fov/peak
    Parameters
    ----------
    fov_id : int corresponding to FOV
    peak_id : int correstonging to peak
    '''

    fCells = {} # f is for filtered

    for cell_id in Cells:
        if Cells[cell_id].fov == fov_id and Cells[cell_id].peak == peak_id:
            fCells[cell_id] = Cells[cell_id]

    return fCells

def plot_lineage_images(Cells, fov_id, peak_id, Cells2=None, bgcolor='c1', fgcolor='seg', plot_tracks=True, trim_time=False, time_set=(0,100), t_adj=1):
    '''
    Plot linages over images across time points for one FOV/peak.
    Parameters
    ----------
    bgcolor : Designation of background to use. Subtracted images look best if you have them.
    fgcolor : Designation of foreground to use. This should be a segmented image.
    Cells2 : second set of linages to overlay. Useful for comparing lineage output.
    plot_tracks : bool
        If to plot cell traces or not.
    t_adj : int
        adjust time indexing for differences between t index of image and image number
    '''

    # filter cells
    Cells = find_cells_of_fov_and_peak(Cells, fov_id, peak_id)

    # load subtracted and segmented data
    image_data_bg = load_stack(fov_id, peak_id, color=bgcolor)

    if fgcolor:
        image_data_seg = load_stack(fov_id, peak_id, color=fgcolor)

    if trim_time:
        image_data_bg = image_data_bg[time_set[0]:time_set[1]]
        if fgcolor:
            image_data_seg = image_data_seg[time_set[0]:time_set[1]]

    n_imgs = image_data_bg.shape[0]
    image_indicies = range(n_imgs)

    if fgcolor:
        # calculate the regions across the segmented images
        regions_by_time = [regionprops(timepoint) for timepoint in image_data_seg]

        # Color map for good label colors
        vmin = 0.5 # values under this color go to black
        vmax = 100 # max y value
        cmap = mpl.colors.ListedColormap(sns.husl_palette(vmax, h=0.5, l=.8, s=1))
        cmap.set_under(color='black')

    # Trying to get the image size down
    figxsize = image_data_bg.shape[2] * n_imgs / 100.0
    figysize = image_data_bg.shape[1] / 100.0

    # plot the images in a series
    fig, axes = plt.subplots(ncols=n_imgs, nrows=1,
                             figsize=(figxsize, figysize),
                             facecolor='black', edgecolor='black')
    fig.subplots_adjust(wspace=0, hspace=0, left=0, right=1, top=1, bottom=0)
    transFigure = fig.transFigure.inverted()

    # change settings for each axis
    ax = axes.flat # same as axes.ravel()
    for a in ax:
        a.set_axis_off()
        a.set_aspect('equal')
        ttl = a.title
        ttl.set_position([0.5, 0.05])

    for i in image_indicies:
        ax[i].imshow(image_data_bg[i], cmap=plt.cm.gray, aspect='equal')

        if fgcolor:
            # make a new version of the segmented image where the
            # regions are relabeled by their y centroid position.
            # scale it so it falls within 100.
            seg_relabeled = image_data_seg[i].copy().astype(np.float)
            for region in regions_by_time[i]:
                rescaled_color_index = region.centroid[0]/image_data_seg.shape[1] * vmax
                seg_relabeled[seg_relabeled == region.label] = int(rescaled_color_index)-0.1 # subtract small value to make it so there is not overlabeling
            ax[i].imshow(seg_relabeled, cmap=cmap, alpha=0.5, vmin=vmin, vmax=vmax)

        ax[i].set_title(str(i + t_adj), color='white')

    # save just the segmented images
    # lin_dir = params['experiment_directory'] + params['analysis_directory'] + 'lineages/'
    # if not os.path.exists(lin_dir):
    #     os.makedirs(lin_dir)
    # lin_filename = params['experiment_name'] + '_xy%03d_p%04d_nolin.png' % (fov_id, peak_id)
    # lin_filepath = lin_dir + lin_filename
    # fig.savefig(lin_filepath, dpi=75)
    # plt.close()

    # Annotate each cell with information
    if plot_tracks:
        for cell_id in Cells:
            for n, t in enumerate(Cells[cell_id].times):
                t -= t_adj # adjust for special indexing

                # don't look at time points out of the interval
                if trim_time:
                    if t < time_set[0] or t >= time_set[1]-1:
                        break

                x = Cells[cell_id].centroids[n][1]
                y = Cells[cell_id].centroids[n][0]

                # add a circle at the centroid for every point in this cell's life
                circle = mpatches.Circle(xy=(x, y), radius=2, color='white', lw=0, alpha=0.5)
                ax[t].add_patch(circle)

                # draw connecting lines between the centroids of cells in same lineage
                try:
                    if n < len(Cells[cell_id].times) - 1:
                        # coordinates of the next centroid
                        x_next = Cells[cell_id].centroids[n+1][1]
                        y_next = Cells[cell_id].centroids[n+1][0]
                        t_next = Cells[cell_id].times[n+1] - t_adj # adjust for special indexing

                        # get coordinates for the whole figure
                        coord1 = transFigure.transform(ax[t].transData.transform([x, y]))
                        coord2 = transFigure.transform(ax[t_next].transData.transform([x_next, y_next]))

                        # create line
                        line = mpl.lines.Line2D((coord1[0], coord2[0]), (coord1[1], coord2[1]),
                                                transform=fig.transFigure,
                                                color='white', lw=1, alpha=0.5)

                        # add it to plot
                        fig.lines.append(line)
                except:
                    pass


                # draw connecting between mother and daughters
                try:
                    if n == len(Cells[cell_id].times)-1 and Cells[cell_id].daughters:
                        # daughter ids
                        d1_id = Cells[cell_id].daughters[0]
                        d2_id = Cells[cell_id].daughters[1]

                        # both daughters should have been born at the same time.
                        t_next = Cells[d1_id].times[0] - t_adj

                        # coordinates of the two daughters
                        x_d1 = Cells[d1_id].centroids[0][1]
                        y_d1 = Cells[d1_id].centroids[0][0]
                        x_d2 = Cells[d2_id].centroids[0][1]
                        y_d2 = Cells[d2_id].centroids[0][0]

                        # get coordinates for the whole figure
                        coord1 = transFigure.transform(ax[t].transData.transform([x, y]))
                        coordd1 = transFigure.transform(ax[t_next].transData.transform([x_d1, y_d1]))
                        coordd2 = transFigure.transform(ax[t_next].transData.transform([x_d2, y_d2]))

                        # create line and add it to plot for both
                        for coord in [coordd1, coordd2]:
                            line = mpl.lines.Line2D((coord1[0],coord[0]),(coord1[1],coord[1]),
                                                    transform=fig.transFigure,
                                                    color='white', lw=1, alpha=0.5, ls='dashed')
                            # add it to plot
                            fig.lines.append(line)
                except:
                    pass

        # this is for plotting the traces from a second set of cells
        if Cells2 and plot_tracks:
            Cells2 = find_cells_of_fov_and_peak(Cells2, fov_id, peak_id)
            for cell_id in Cells2:
                for n, t in enumerate(Cells2[cell_id].times):
                    t -= t_adj

                    # don't look at time points out of the interval
                    if trim_time:
                        if t < time_set[0] or t >= time_set[1]-1:
                            break

                    x = Cells2[cell_id].centroids[n][1]
                    y = Cells2[cell_id].centroids[n][0]

                    # add a circle at the centroid for every point in this cell's life
                    circle = mpatches.Circle(xy=(x, y), radius=2, color='yellow', lw=0, alpha=0.25)
                    ax[t].add_patch(circle)

                    # draw connecting lines between the centroids of cells in same lineage
                    try:
                        if n < len(Cells2[cell_id].times) - 1:
                            # coordinates of the next centroid
                            x_next = Cells2[cell_id].centroids[n+1][1]
                            y_next = Cells2[cell_id].centroids[n+1][0]
                            t_next = Cells2[cell_id].times[n+1] - t_adj

                            # get coordinates for the whole figure
                            coord1 = transFigure.transform(ax[t].transData.transform([x, y]))
                            coord2 = transFigure.transform(ax[t_next].transData.transform([x_next, y_next]))

                            # create line
                            line = mpl.lines.Line2D((coord1[0],coord2[0]),(coord1[1],coord2[1]),
                                                    transform=fig.transFigure,
                                                    color='yellow', lw=1, alpha=0.25)

                            # add it to plot
                            fig.lines.append(line)
                    except:
                        pass

                    # draw connecting between mother and daughters
                    try:
                        if n == len(Cells2[cell_id].times)-1 and Cells2[cell_id].daughters:
                            # daughter ids
                            d1_id = Cells2[cell_id].daughters[0]
                            d2_id = Cells2[cell_id].daughters[1]

                            # both daughters should have been born at the same time.
                            t_next = Cells2[d1_id].times[0] - t_adj

                            # coordinates of the two daughters
                            x_d1 = Cells2[d1_id].centroids[0][1]
                            y_d1 = Cells2[d1_id].centroids[0][0]
                            x_d2 = Cells2[d2_id].centroids[0][1]
                            y_d2 = Cells2[d2_id].centroids[0][0]

                            # get coordinates for the whole figure
                            coord1 = transFigure.transform(ax[t].transData.transform([x, y]))
                            coordd1 = transFigure.transform(ax[t_next].transData.transform([x_d1, y_d1]))
                            coordd2 = transFigure.transform(ax[t_next].transData.transform([x_d2, y_d2]))

                            # create line and add it to plot for both
                            for coord in [coordd1, coordd2]:
                                line = mpl.lines.Line2D((coord1[0],coord[0]),(coord1[1],coord[1]),
                                                        transform=fig.transFigure,
                                                        color='yellow', lw=1, alpha=0.25, ls='dashed')
                                # add it to plot
                                fig.lines.append(line)
                    except:
                        pass

            # this is for putting cell id on first time cell appears and when it divides
            # this is broken, need to translate coordinates to correct location on figure.
            # if n == 0 or n == len(Cells[cell_id].times)-1:
            #     print(x/100.0, y/100.0, cell_id[9:])
            #     ax[t].text(x/100.0, y/100.0, cell_id[9:], color='red', size=10, ha='center', va='center')

    return fig, ax

def find_cells_of_birth_label(Cells, label_num=1):
    '''Return only cells whose starting region label is given.
    If no birth_label is given, returns the mother cells.
    label_num can also be a list to include cells of many birth labels
    '''

    fCells = {} # f is for filtered

    if type(label_num) is int:
        label_num = [label_num]

    for cell_id in Cells:
        if Cells[cell_id].birth_label in label_num:
            fCells[cell_id] = Cells[cell_id]

    return fCells

def Lineage(params,numSamples=10):
    # plotting lineage trees for complete cells
    # load specs file
    with open(os.path.join(params['ana_dir'], 'specs.yaml'), 'r') as specs_file:
        specs = yaml.safe_load(specs_file)
    with open(os.path.join(params['cell_dir'], 'all_cells.pkl'), 'rb') as cell_file:
        Cells = pickle.load(cell_file)
    with open(os.path.join(params['cell_dir'], 'complete_cells.pkl'), 'rb') as cell_file:
        Cells2 = pickle.load(cell_file)
        Cells2 = find_cells_of_birth_label(Cells2, label_num=[1,2])

    lin_dir = os.path.join(params['experiment_directory'], params['analysis_directory'],
                        'lineages')
    if not os.path.exists(lin_dir):
        os.makedirs(lin_dir)

    # determine number of lineages to make
    no_samples = numSamples
    # list contains tuples of (fov_id, peak_id, timepoint)
    sample_ids = []
    for fov_id in specs.keys():
        for peak_id, spec in specs[fov_id].items():
            if spec == 1:
                # now put the fov, peak, and a random timepoint in a tuple
                sample_ids.append((fov_id, peak_id))

    # choose a random subset of these and sort them
    sample_indicies = np.random.choice(len(sample_ids), no_samples)
    sample_ids = sorted([sample_ids[i] for i in sample_indicies])

    for sample in sample_ids:
        fov_id, peak_id = sample
        fig, ax = plot_lineage_images(Cells, fov_id, peak_id, Cells2,
                                                bgcolor=params['phase_plane'])
        lin_filename = params['experiment_name'] + '_xy%03d_p%04d_lin.png' % (fov_id, peak_id)
        lin_filepath = os.path.join(lin_dir, lin_filename)
        fig.savefig(lin_filepath, dpi=75)
        img = image.imread(lin_filepath)
        napari.current_viewer().add_image(img, name=lin_filename, visible=False)
        plt.close(fig)

    information("Completed Plotting")

def Compile(experiment_name: str='exp1', experiment_directory: str= '/Users/sharan/Desktop/exp1/', image_directory:str='TIFF/', external_directory: str= '/Users/sharan/Desktop/exp1/',  analysis_directory:str= 'analysis/', FOV:str='1-5', TIFF_source:str='nd2ToTIFF',
output:str='TIFF', debug:str= False, pxl2um:float= 0.11, phase_plane: str ='c1', image_start : int=1, number_of_rows :int = 1, tiff_compress:int=5,
do_metadata: bool=True, do_time_table: bool=True, do_channel_masks: bool=True, do_slicing:bool=True, find_channels_method:str='peaks',
image_orientation : str= 'up', channel_width : int=10, channel_separation : int=45, channel_detection_snr : int=1, channel_length_pad : int=10,
channel_width_pad : int=10, trap_crop_height: int=256, trap_crop_width: int=27, trap_area_threshold: int=2, channel_prediction_batch_size: int=15,
merged_trap_region_area_threshold: int=400):
    """Performs Mother Machine Analysis"""

    global params
    params=dict()
    params['experiment_name']=experiment_name
    params['experiment_directory']=experiment_directory
    params['image_directory']=image_directory
    params['analysis_directory']=analysis_directory
    params['FOV']=FOV
    params['TIFF_source']=TIFF_source
    params['output']=output
    params['debug']=debug
    params['phase_plane']=phase_plane
    params['pxl2um']=pxl2um
    params['nd2ToTIFF']=dict()
    params['nd2ToTIFF']['image_start']=image_start
    params['nd2ToTIFF']['image_end']=None
    params['nd2ToTIFF']['number_of_rows']=number_of_rows
    params['nd2ToTIFF']['crop_ymin']=None
    params['nd2ToTIFF']['crop_ymax']=None
    params['nd2ToTIFF']['2row_crop']=None
    params['nd2ToTIFF']['tiff_compress']=tiff_compress
    params['nd2ToTIFF']['external_directory']=external_directory
    params['compile']=dict()
    params['compile']['do_metadata' ]=do_metadata
    params['compile']['do_time_table']=do_time_table
    params['compile']['do_channel_masks']=do_channel_masks
    params['compile']['do_slicing']=do_slicing
    params['compile']['t_end']=None
    params['compile']['find_channels_method']=find_channels_method
    #model_file_traps: str='/Users/sharan/Desktop/Physics/mm3-latest/weights/feature_weights_512x512_normed.hdf5',
    params['compile']['model_file_traps']='/Users/sharan/Desktop/Physics/mm3-latest/weights/feature_weights_512x512_normed.hdf5'
    params['compile']['image_orientation']=image_orientation
    params['compile']['channel_width']=channel_width
    params['compile']['channel_separation']=channel_separation
    params['compile']['channel_detection_snr']=channel_detection_snr
    params['compile']['channel_length_pad']=channel_length_pad
    params['compile']['channel_width_pad']=channel_width_pad
    params['compile']['trap_crop_height']=trap_crop_height
    params['compile']['trap_crop_width']=trap_crop_width
    params['compile']['trap_area_threshold']=trap_area_threshold*1000
    params['compile']['channel_prediction_batch_size']=channel_prediction_batch_size
    params['compile']['merged_trap_region_area_threshold']=merged_trap_region_area_threshold*1000

    params['num_analyzers'] = multiprocessing.cpu_count()

    # useful folder shorthands for opening files
    params['TIFF_dir'] = os.path.join(params['experiment_directory'], params['image_directory'])
    params['ana_dir'] = os.path.join(params['experiment_directory'], params['analysis_directory'])
    params['hdf5_dir'] = os.path.join(params['ana_dir'], 'hdf5')
    params['chnl_dir'] = os.path.join(params['ana_dir'], 'channels')
    params['empty_dir'] = os.path.join(params['ana_dir'], 'empties')
    params['sub_dir'] = os.path.join(params['ana_dir'], 'subtracted')
    params['seg_dir'] = os.path.join(params['ana_dir'], 'segmented')
    params['pred_dir'] = os.path.join(params['ana_dir'], 'predictions')
    params['foci_seg_dir'] = os.path.join(params['ana_dir'], 'segmented_foci')
    params['foci_pred_dir'] = os.path.join(params['ana_dir'], 'predictions_foci')
    params['cell_dir'] = os.path.join(params['ana_dir'], 'cell_data')
    params['track_dir'] = os.path.join(params['ana_dir'], 'tracking')
    params['foci_track_dir'] = os.path.join(params['ana_dir'], 'tracking_foci')

    # use jd time in image metadata to make time table. Set to false if no jd time
    if params['TIFF_source'] == 'elements' or params['TIFF_source'] == 'nd2ToTIFF':
        params['use_jd'] = True
    else:
        params['use_jd'] = False

    nd2ToTIFF(params)
    compile(params)
    return

def ChannelPicker(experiment_name: str='exp1', experiment_directory: str= '/Users/sharan/Desktop/exp1/', image_directory:str='TIFF/', external_directory: str= '/Users/sharan/Desktop/exp1/',  analysis_directory:str= 'analysis/', FOV:str='1-5', TIFF_source:str='nd2ToTIFF',
output:str='TIFF', debug:str= False, pxl2um:float= 0.11, phase_plane: str ='c1', do_crosscorrs:bool=True, do_CNN:bool=False, interactive:bool=True, do_seg:bool=False,
first_image: int=1, channel_picking_threshold: float =0.99, channel_picker_model_file='/Users/sharan/Desktop/Physics/mm3-latest/weights/empties_weights.hdf5', do_empties:bool=True, do_subtraction: bool=True, alignment_pad: int=10, selection_done:bool=False):
    """Performs Mother Machine Analysis"""

    global params
    params=dict()
    params['experiment_name']=experiment_name
    params['experiment_directory']=experiment_directory
    params['image_directory']=image_directory
    params['analysis_directory']=analysis_directory
    params['FOV']=FOV
    params['TIFF_source']=TIFF_source
    params['output']=output
    params['debug']=debug
    params['phase_plane']=phase_plane
    params['pxl2um']=pxl2um
    params['subtract']=dict()
    params['subtract']['do_empties']=do_empties
    params['subtract']['do_subtraction']=do_subtraction
    params['subtract']['alignment_pad']=alignment_pad
    params['channel_picker']=dict()
    params['channel_picker']['do_crosscorrs']=do_crosscorrs
    params['channel_picker']['do_CNN']=do_CNN
    params['channel_picker']['interactive']=interactive
    params['channel_picker']['do_seg']=do_seg
    params['channel_picker']['first_image']=first_image
    params['channel_picker']['last_image']=-1
    params['channel_picker']['channel_picking_threshold']=channel_picking_threshold
    params['channel_picker']['channel_picker_model_file']=channel_picker_model_file

    params['num_analyzers'] = multiprocessing.cpu_count()

    # useful folder shorthands for opening files
    params['TIFF_dir'] = os.path.join(params['experiment_directory'], params['image_directory'])
    params['ana_dir'] = os.path.join(params['experiment_directory'], params['analysis_directory'])
    params['hdf5_dir'] = os.path.join(params['ana_dir'], 'hdf5')
    params['chnl_dir'] = os.path.join(params['ana_dir'], 'channels')
    params['empty_dir'] = os.path.join(params['ana_dir'], 'empties')
    params['sub_dir'] = os.path.join(params['ana_dir'], 'subtracted')
    params['seg_dir'] = os.path.join(params['ana_dir'], 'segmented')
    params['pred_dir'] = os.path.join(params['ana_dir'], 'predictions')
    params['foci_seg_dir'] = os.path.join(params['ana_dir'], 'segmented_foci')
    params['foci_pred_dir'] = os.path.join(params['ana_dir'], 'predictions_foci')
    params['cell_dir'] = os.path.join(params['ana_dir'], 'cell_data')
    params['track_dir'] = os.path.join(params['ana_dir'], 'tracking')
    params['foci_track_dir'] = os.path.join(params['ana_dir'], 'tracking_foci')
    params['channel_select_dir']= os.path.join(params['ana_dir'], 'channel_picker')

    # use jd time in image metadata to make time table. Set to false if no jd time
    if params['TIFF_source'] == 'elements' or params['TIFF_source'] == 'nd2ToTIFF':
        params['use_jd'] = True
    else:
        params['use_jd'] = False

    if selection_done:
        channelProcessor(params)
    else:
        channelPicker(params)
    return

def Segment(experiment_name: str='exp1', experiment_directory: str= '/Users/sharan/Desktop/exp1/', model_file: str='/Users/sharan/Desktop/exp1/20200921_MG1655_256x32.hdf5', image_directory:str='TIFF/', external_directory:str= '/Users/sharan/Desktop/exp1/',  analysis_directory:str= 'analysis/', FOV:str='1-5', TIFF_source:str='nd2ToTIFF',
output:str='TIFF', debug:str= False, pxl2um:float= 0.11, phase_plane: str ='c1', do_empties:bool=True, do_subtraction: bool=True, alignment_pad: int=10, do_segmentation=True, do_lineages=True,  OTSU_threshold: float= 1.0, first_opening_size: int=2,
distance_threshold: int=2, second_opening_size: int=1, min_object_size:int= 25, trained_model_image_height: int=256, trained_model_image_width: int=32,
batch_size: int=210, cell_class_threshold: float= 0.60, normalize_to_one:bool= False, save_predictions:bool=False, OTSU :bool=True, UNet: bool=False):
    """Performs Mother Machine Analysis"""

    global params
    params=dict()
    params['experiment_name']=experiment_name
    params['experiment_directory']=experiment_directory
    params['image_directory']=image_directory
    params['analysis_directory']=analysis_directory
    params['external_directory']=external_directory
    params['FOV']=FOV
    params['TIFF_source']=TIFF_source
    params['output']=output
    params['debug']=debug
    params['phase_plane']=phase_plane
    params['pxl2um']=pxl2um
    params['subtract']=dict()
    params['subtract']['do_empties']=do_empties
    params['subtract']['do_subtraction']=do_subtraction
    params['subtract']['alignment_pad']=alignment_pad
    params['segment']=dict()
    params['segment']['do_segmentation']=do_segmentation
    params['segment']['do_lineages']=do_lineages
    params['segment']['OTSU_threshold']=OTSU_threshold
    params['segment']['first_opening_size']=first_opening_size
    params['segment']['distance_threshold']=distance_threshold
    params['segment']['second_opening_size']=second_opening_size
    params['segment']['min_object_size']=min_object_size
    params['segment']['model_file']=model_file
    params['segment']['trained_model_image_height']=trained_model_image_height
    params['segment']['trained_model_image_width']=trained_model_image_width
    params['segment']['batch_size']=batch_size
    params['segment']['cell_class_threshold']=cell_class_threshold
    params['segment']['save_predictions']=save_predictions
    params['segment']['normalize_to_one']=normalize_to_one
    params['num_analyzers'] = multiprocessing.cpu_count()

    # useful folder shorthands for opening files
    params['TIFF_dir'] = os.path.join(params['experiment_directory'], params['image_directory'])
    params['ana_dir'] = os.path.join(params['experiment_directory'], params['analysis_directory'])
    params['hdf5_dir'] = os.path.join(params['ana_dir'], 'hdf5')
    params['chnl_dir'] = os.path.join(params['ana_dir'], 'channels')
    params['empty_dir'] = os.path.join(params['ana_dir'], 'empties')
    params['sub_dir'] = os.path.join(params['ana_dir'], 'subtracted')
    params['seg_dir'] = os.path.join(params['ana_dir'], 'segmented')
    params['pred_dir'] = os.path.join(params['ana_dir'], 'predictions')
    params['foci_seg_dir'] = os.path.join(params['ana_dir'], 'segmented_foci')
    params['foci_pred_dir'] = os.path.join(params['ana_dir'], 'predictions_foci')
    params['cell_dir'] = os.path.join(params['ana_dir'], 'cell_data')
    params['track_dir'] = os.path.join(params['ana_dir'], 'tracking')
    params['foci_track_dir'] = os.path.join(params['ana_dir'], 'tracking_foci')

    # use jd time in image metadata to make time table. Set to false if no jd time
    if params['TIFF_source'] == 'elements' or params['TIFF_source'] == 'nd2ToTIFF':
        params['use_jd'] = True
    else:
        params['use_jd'] = False

    subtract(params)

    if OTSU:
        params['seg_img'] = 'seg_otsu'
        segmentOTSU(params)

    if UNet:
        params['seg_img'] = 'seg_unet'
        params['pred_img'] = 'pred_unet'
        segmentUNet(params)

    return

def Track_Standard(experiment_name: str='exp1', experiment_directory: str= '/Users/sharan/Desktop/exp1/', image_directory:str='TIFF/', external_directory:str= '/Users/sharan/Desktop/exp1/',  analysis_directory:str= 'analysis/', FOV:str='1-5', TIFF_source:str='nd2ToTIFF',
output:str='TIFF', debug:str= False, pxl2um:float= 0.11, phase_plane: str ='c1', lost_cell_time:int= 3, new_cell_y_cutoff:int= 150, new_cell_region_cutoff:float= 4, max_growth_length:float= 1.5, min_growth_length:float= 0.7, max_growth_area:float= 1.5, min_growth_area:float= 0.7 , numSamples:int=10, seg_img :str='seg_otsu'):
    """Performs Mother Machine Analysis"""

    global params
    params=dict()
    params['experiment_name']=experiment_name
    params['experiment_directory']=experiment_directory
    params['image_directory']=image_directory
    params['analysis_directory']=analysis_directory
    params['external_directory']=external_directory
    params['FOV']=FOV
    params['TIFF_source']=TIFF_source
    params['output']=output
    params['debug']=debug
    params['phase_plane']=phase_plane
    params['pxl2um']=pxl2um
    params['num_analyzers'] = multiprocessing.cpu_count()
    params['track']=dict()
    params['track']['lost_cell_time']= lost_cell_time
    params['track']['new_cell_y_cutoff']= new_cell_y_cutoff
    params['track']['new_cell_region_cutoff']= new_cell_region_cutoff
    params['track']['max_growth_length']= max_growth_length
    params['track']['min_growth_length']= min_growth_length
    params['track']['max_growth_area']= max_growth_area
    params['track']['min_growth_area']= min_growth_area
    params['track']['seg_img']=seg_img

    # useful folder shorthands for opening files
    params['TIFF_dir'] = os.path.join(params['experiment_directory'], params['image_directory'])
    params['ana_dir'] = os.path.join(params['experiment_directory'], params['analysis_directory'])
    params['hdf5_dir'] = os.path.join(params['ana_dir'], 'hdf5')
    params['chnl_dir'] = os.path.join(params['ana_dir'], 'channels')
    params['empty_dir'] = os.path.join(params['ana_dir'], 'empties')
    params['sub_dir'] = os.path.join(params['ana_dir'], 'subtracted')
    params['seg_dir'] = os.path.join(params['ana_dir'], 'segmented')
    params['pred_dir'] = os.path.join(params['ana_dir'], 'predictions')
    params['foci_seg_dir'] = os.path.join(params['ana_dir'], 'segmented_foci')
    params['foci_pred_dir'] = os.path.join(params['ana_dir'], 'predictions_foci')
    params['cell_dir'] = os.path.join(params['ana_dir'], 'cell_data')
    params['track_dir'] = os.path.join(params['ana_dir'], 'tracking')
    params['foci_track_dir'] = os.path.join(params['ana_dir'], 'tracking_foci')

    Track(params)
    Lineage(params, numSamples)
    return
