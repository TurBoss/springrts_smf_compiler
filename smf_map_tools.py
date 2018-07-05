#!/usr/bin/env python
# -*- coding: utf-8 -*-

# PD license by Beherith
# You like pasghetti code? No problem, you get pasghetti code.

from __future__ import print_function

import sys
import struct
from PIL import Image
import png
import random
import os
import gc

print('Welcome to the SMF compiler/decompiler by Beherith (mysterme@gmail.com)')

SMFHeader_struct = struct.Struct('< 16s i i i i i i i f f i i i i i i i')
'''	char magic[16];      ///< "spring map file\0"
	int version;         ///< Must be 1 for now
	int mapid;           ///< Sort of a GUID of the file, just set to a random value when writing a map

	int mapx;            ///< Must be divisible by 128
	int mapy;            ///< Must be divisible by 128
	int squareSize;      ///< Distance between vertices. Must be 8
	int texelPerSquare;  ///< Number of texels per square, must be 8 for now
	int tilesize;        ///< Number of texels in a tile, must be 32 for now
	float minHeight;     ///< Height value that 0 in the heightmap corresponds to
	float maxHeight;     ///< Height value that 0xffff in the heightmap corresponds to

	int heightmapPtr;    ///< File offset to elevation data (short int[(mapy+1)*(mapx+1)])
	int typeMapPtr;      ///< File offset to typedata (unsigned char[mapy/2 * mapx/2])
	int tilesPtr;        ///< File offset to tile data (see MapTileHeader)
	int minimapPtr;      ///< File offset to minimap (always 1024*1024 dxt1 compresed data plus 8 mipmap sublevels)
	int metalmapPtr;     ///< File offset to metalmap (unsigned char[mapx/2 * mapy/2])
	int featurePtr;      ///< File offset to feature data (see MapFeatureHeader)

	int numExtraHeaders; ///< Numbers of extra headers following main header
'''
ExtraHeader_struct = struct.Struct('< i i i')
'''	int size; ///< Size of extra header
	int type; ///< Type of extra header
	int extraoffset ; //MISSING FROM DOCS, only exists if type=1 (vegmap)'''
MapTileHeader_struct = struct.Struct('< i i')
'''	int numTileFiles; ///< Number of tile files to read in (usually 1)
	int numTiles;     ///< Total number of tiles'''
MapFeatureHeader_struct = struct.Struct('< i i')
'''	int numFeatureType;
	int numFeatures;'''

MapFeatureStruct_struct = struct.Struct('< i f f f f f')
'''int featureType;    ///< Index to one of the strings above
	float xpos;         ///< X coordinate of the feature
	float ypos;         ///< Y coordinate of the feature (height)
	float zpos;         ///< Z coordinate of the feature

	float rotation;     ///< Orientation of this feature (-32768..32767 for full circle)
	float relativeSize; ///< Not used at the moment keep 1'''
TileFileHeader_struct = struct.Struct('< 16s i i i i')
'''	char magic[16];      ///< "spring tilefile\0"
	int version;         ///< Must be 1 for now

	int numTiles;        ///< Total number of tiles in this file
	int tileSize;        ///< Must be 32 for now
	int compressionType; ///< Must be 1 (= dxt1) for now'''

SMALL_TILE_SIZE = 680
MINIMAP_SIZE = 699048


def pythonDecodeDXT1(data):  # Python-only DXT1 decoder; this is slow!
    # input: one "row" of data (i.e. will produce 4*width pixels)
    blocks = len(data) / 8  # number of blocks in row
    out = ['', '', '', '']  # row accumulators

    for xb in range(blocks):
        # Decode next 8-byte block.
        c0, c1, bits = struct.unpack('<HHI', data[xb * 8:xb * 8 + 8])
        # print c0,c1,bits
        # color 0, packed 5-6-5
        b0 = (c0 & 0x1f) << 3
        g0 = ((c0 >> 5) & 0x3f) << 2
        r0 = ((c0 >> 11) & 0x1f) << 3

        # color 1, packed 5-6-5
        b1 = (c1 & 0x1f) << 3
        g1 = ((c1 >> 5) & 0x3f) << 2
        r1 = ((c1 >> 11) & 0x1f) << 3

        # Decode this block into 4x4 pixels
        # Accumulate the results onto our 4 row accumulators
        for yo in range(4):
            for xo in range(4):
                # get next control op and generate a pixel

                control = bits & 3
                bits = bits >> 2
                if control == 0:
                    out[yo] += chr(r0) + chr(g0) + chr(b0)
                elif control == 1:
                    out[yo] += chr(r1) + chr(g1) + chr(b1)
                elif control == 2:
                    if c0 > c1:
                        out[yo] += chr((2 * r0 + r1 + 1) / 3) + chr((2 * g0 + g1 + 1) / 3) + chr((2 * b0 + b1 + 1) / 3)
                    else:
                        out[yo] += chr((r0 + r1) / 2) + chr((g0 + g1) / 2) + chr((b0 + b1) / 2)
                elif control == 3:
                    if c0 > c1:
                        out[yo] += chr((2 * r1 + r0 + 1) / 3) + chr((2 * g1 + g0 + 1) / 3) + chr((2 * b1 + b0 + 1) / 3)
                    else:
                        out[yo] += '\0\0\0'

    # All done.
    return out


def pythonEncodeDXT1(data):
    # color bounding box algorithm, this is absolutely trashy and slow and only exists because im too lazy to include a linux compressor
    # also, i havent even tested this yet, dont even think about using it
    # we are expecting 16 RGB or RGBA pixels
    def rgbto565(pix):
        return struct.pack('<H', (int(pix[0]) / 8) * (2 ** 11) + (int(pix[1]) / 4) * (2 ** 5) + (int(pix[2]) / 8))

    def diff(a, b):
        d = 0
        for i in range(len(a)):
            d += abs(a[i] - b[i])
        return d

    havealpha = False
    if len(data[0]) > 3:
        havealpha = True
    weights = (1.0, 1.0, 1.0)
    weighted = []
    mins = [10.0, 10.0, 10.0]
    maxes = [0.0, 0.0, 0.0]
    for pixel in data:
        newpix = [0, 0, 0]
        for c in range(3):
            newpix[c] = pixel[c] * weights[c]
            mins[c] = min(newpix[c], mins[c])
            maxes[c] = max(newpix[c], mins[c])
        weighted.append(newpix)
    # rescale BB by 10% (trash)
    # for c in range(3):
    #	mins[c]=mins[c]*1.1
    #	maxes[c]=maxes[c]*1.1
    outchunk = b'' + rgbto565(maxes) + rgbto565(mins)
    c2 = [0, 0, 0]
    c3 = [0, 0, 0]
    for i in range(3):
        c2[i] = 0.666 * maxes[i] + 0.333 * mins[i]
        c3[i] = 0.333 * maxes[i] + 0.666 * mins[i]
    lookup = []
    best = -1
    bestdiff = 100000
    for pixel in weighted:
        if diff(pixel, maxes) < bestdiff:
            bestdiff = diff(pixel, maxes)
            best = 0
        if diff(pixel, mins) < bestdiff:
            bestdiff = diff(pixel, mins)
            best = 1
        if diff(pixel, c2) < bestdiff:
            bestdiff = diff(pixel, c2)
            best = 2
        if diff(pixel, c3) < bestdiff:
            bestdiff = diff(pixel, c3)
            best = 3
        lookup.append(best)
    lookup32 = 0L
    for i in range(len(data)):
        lookup32 += lookup[i] * (2 ** (2 * i))
    return outchunk + struct.pack('<I', lookup32)  # archer-esque 'woooooo'


def unpack_null_terminated_string(data, offset):
    result = ''
    # nextchar = 'X'
    while True:
        if len(data) <= offset + len(result):
            raise Exception(
                "Failed to read a null terminated string from input file because the offset is past the end of the file! Last result:" + result)
        nextchar = struct.unpack_from('c', data, offset + len(result))[0]
        if nextchar == '\0':
            return result
        else:
            result += nextchar
        if len(result) > 256:
            return result


def compileSMF(myargs):
    verbose = True

    print('Compiling SMF with the following options:', myargs)
    if myargs.outfile == '':
        print('Please specify a name for the map!')
        return

    if '.smf' not in myargs.outfile:
        myargs.outfile += '.smf'
        print('The .smf extension was omitted from the output file name, output will be:', myargs.outfile)

    # open texture, get sizes

    Image.MAX_IMAGE_PIXELS = None
    intex = Image.open(myargs.intex)
    intex_pixels = intex.load()
    texw, texh = intex.size
    mapx = texw / 8
    mapy = texh / 8
    springmapx = texw / 512
    springmapy = texh / 512
    if (texh % 1024 != 0) or (texw % 1024 != 0):
        print('Error: Texture Image dimensions are not multiples of 1024! (%ix%i) Aborting' % (texw, texh))
        return
    else:
        print('Texture image %s seems to have the correct dimensions (%ix%i) for a spring map size of (%ix%i)' % (
            myargs.intex, texw, texh, springmapx, springmapy))
    # do some checks for alpha in texture
    if intex.mode == 'RGB':
        print('Texture image %s is RGB, just making sure you dont have alpha in it...' % (myargs.intex))
    elif intex.mode == 'RGBA':
        print(
            'Texture image %s is RGBA, thus has an alpha channel. Make absolutely sure that you need this or else consider removing the alpha, as this can cause undesired artefacts with voidground or voidwater tags!' % (
                myargs.intex))
    else:
        print('Texture image %s is neither RGB nor RGBA, but is %s this may cause unexpected issues downstream!' % (
            myargs.intex, intex.mode))
    # open heightmap:
    heights = []
    if '.raw' in myargs.heightmap.lower():
        rawheight = open(myargs.heightmap, 'rb').read()
        expectedheightmapsize = (mapx + 1) * (mapy + 1) * 2
        if len(rawheight) != expectedheightmapsize:
            print(
                'Error: Incorrect %s heightmap dimensions, file size should be exactly %i (%ix%i) for a spring map size of (%ix%i)' % (
                    myargs.heightmap, expectedheightmapsize, mapx + 1, mapy + 1, springmapx, springmapy))
            return
        else:
            heights = struct.unpack('< ' + 'H' * (expectedheightmapsize / 2), rawheight)
    elif '.png' in myargs.heightmap.lower():

        pngheight = png.Reader(
            filename=myargs.heightmap)  # (32, 32, <itertools.imap object at 0x10b7eb0>, {'greyscale': True,'alpha': False, 'interlace': 0, 'bitdepth': 2, 'gamma': 1.0})
        pngheight = pngheight.read()
        if pngheight[0] * pngheight[1] != (mapx + 1) * (mapy + 1):
            print(
                'Error: Incorrect %s heightmap dimensions of (%ix%i), image size should be exactly %ix%i for a spring map size of (%ix%i)' % (
                    myargs.heightmap, pngheight[0], pngheight[1], mapx + 1, mapy + 1, springmapx, springmapy))
            return
        if pngheight[3]['bitdepth'] != 16:
            print(
                'Error: heightmap %s must be 16 bit depth, instead it is %i. Dont use .png for 8 bit heightmaps, use .bmp!' % (
                    myargs.heightmap, pngheight[3]['bitdepth']))
            return
        if pngheight[3]['greyscale'] == False:
            print('Error: heightmap %s must be greyscale!' % (myargs.heightmap))
            return
        if pngheight[3]['alpha'] == True:
            print('Error: heightmap %s must not contain an alpha channel!' % (myargs.heightmap))
            return

        # do a check to make sure the full 16-bit range of heights are used!
        heightlevelshist = {}
        for row in pngheight[2]:
            for col in row:
                heights.append(col)
                if col in heightlevelshist:
                    heightlevelshist[col] += 1
                else:
                    heightlevelshist[col] = 1
        print('You are using %i unique height levels in your heightmap.' % (len(heightlevelshist)))
        if len(heightlevelshist) <= 256:
            print(
                'Warning: Even though you have specified a 16-bit heightmap, you are only using %i unique height levels.' % (
                    len(heightlevelshist)))
            print(
                'Warning: This may result in terracing, consider using some surface blur on your heightmap to utilize full 16-bit depth!')
        heightlevelshist = {}
    else:
        print(
            'Warning: you are using an 8-bit heightmap. This will most likely result in terracing effects, so consider switching to 16-bit depth .png!')
        otherheight = Image.open(myargs.heightmap)
        otherheight_pixels = otherheight.load()
        if otherheight.size != (mapx + 1, mapy + 1):
            print(
                'Error: Incorrect %s heightmap dimensions of (%ix%i), image size should be exactly %ix%i for a spring map size of (%ix%i)' % (
                    myargs.heightmap, otherheight.size[0], otherheight.size[1], mapx + 1, mapy + 1, springmapx,
                    springmapy))
            return
        for row in range(otherheight.size[1]):
            for col in range(otherheight.size[0]):
                heights.append(sum(otherheight_pixels[col, row]) * 256 / 3)

    # open metalmap:
    metalmap = []
    if myargs.metalmap:
        metalimage = Image.open(myargs.metalmap)
        if metalimage.size != (mapx / 2, mapy / 2):
            print(
                'Warning: Incorrect %s metal dimensions of (%ix%i), image size should be %ix%i for a spring map size of (%ix%i) ' % (
                    myargs.metalmap, metalimage.size[0], metalimage.size[1], mapx / 2, mapy / 2, springmapx,
                    springmapy))
            print('Rescaling metalmap %s to (%ix%i)' % (myargs.metalmap, mapx / 2, mapy / 2))
            metalimage = metalimage.resize((mapx / 2, mapy / 2), Image.BILINEAR)
        metalimage_pixels = metalimage.load()
        for row in range(metalimage.size[1]):
            for col in range(metalimage.size[0]):
                metalmap.append(metalimage_pixels[col, row][0])
    else:
        metalmap = [0] * (mapy * mapx / 4)

    # if myargs.invert:
    #    print('Flipping heightmap upside down is not implemented yet :(')
    # newheights=[]

    # load features from featureplacement;
    featuretypes = ['TreeType0', 'TreeType1', 'TreeType2', 'TreeType3', 'TreeType4', 'TreeType5', 'TreeType6',
                    'TreeType7', 'TreeType8', 'TreeType9', 'TreeType10', 'TreeType11', 'TreeType12', 'TreeType13',
                    'TreeType14', 'TreeType15', 'GeoVent']
    featureplacement = []
    if myargs.featureplacement:
        for line in open(myargs.featureplacement).readlines():
            line = line.strip().split(',')
            if len(line) < 3:
                continue
            myfeature = {'name': '', 'x': 0.0, 'y': 0.0, 'z': 0.0, 'rot': 0.0, 'scale': 1.0}
            for block in line:
                if '=' in block:
                    block = block.partition('=')
                    key = block[0].strip(' {}\'\"').lower()
                    val = block[2].strip(' {}\'\"')
                    if key == 'name':
                        myfeature[key] = val
                        if val not in featuretypes:
                            featuretypes.append(val)
                    elif key in myfeature:
                        try:
                            myfeature[key] = float(val)
                        except ValueError:
                            print ('Featureplacement: unable to parse line %s for floats at %s' % (str(line), key))
            featureplacement.append(myfeature)

    # load features from featuremap
    featurelist = []
    if myargs.featurelist:
        for line in open(myargs.featurelist).readlines():
            line = line.split(' ')
            if line[0] not in featuretypes:
                featuretypes.append(line[0])
            if len(line) > 1:
                try:
                    featurelist.append((line[0], int(line[1])))
                except ValueError:
                    print ('Failed to parse line %s in featurelist:' % (str(line)))
            else:
                featurelist.append((line[0], 0))
    vegmap = [0] * ((mapx / 4) * (mapy / 4))

    if myargs.featuremap:
        featuremap = Image.open(myargs.featuremap)
        if featuremap.size != (mapx, mapy):
            print(
                'Error: Incorrect %s featuremap dimensions of (%ix%i), image size should be exactly %ix%i for a spring map size of (%ix%i)' % (
                    myargs.featuremap, featuremap.size[0], featuremap.size[1], mapx, mapy, springmapx, springmapy))
            return
        featuremap_pixels = featuremap.load()
        for row in range(featuremap.size[1]):
            for col in range(featuremap.size[0]):
                pixel = featuremap_pixels[col, row]
                if col % 2 and row % 2:  # grass at half rez
                    if random.randint(0, 255) < pixel[2]:
                        vegmap[(mapx / 4) * row + col] = 1

                if pixel[1] == 255:  # geovent
                    featureplacement.append(
                        {'name': 'Geovent', 'x': 8.0 * col + 4, 'y': 0.0, 'z': 8.0 * col + 4, 'rot': 0.0, 'scale': 1.0})
                    print('Placed Geovent: %s' % (str(featureplacement[-1])))
                elif pixel[1] < 216 and pixel[1] > 199:
                    featureplacement.append(
                        {'name': 'TreeType%i' % (pixel[1] - 200), 'x': 8.0 * col + 4, 'y': 0.0, 'z': 8.0 * col + 4,
                         'rot': 0.0, 'scale': 1.0})
                elif pixel[1] != 0:
                    print('Undefined green pixel of value %i at %i x %i in %s. Not placing anything')
                if pixel[0] > 0:
                    try:
                        featureplacement.append(
                            {'name': featurelist[255 - pixel[0]][0], 'x': 8.0 * col + 4, 'y': 0.0, 'z': 8.0 * col + 4,
                             'rot': featuretypes[255 - pixel[0]][1], 'scale': 1.0})
                    except IndexError:
                        print(
                            'Unable to find a featurename in featurelist for red pixel value %i at %ix%i in featuremap!' % (
                                pixel[0], col, row))
    print(
        'Placed a total of %i features out of %i feature types (17 of which are built-in), with the following distribution:' % (
            len(featureplacement), len(featuretypes)))
    for featuretype in featuretypes:
        cnt = 0
        for feature in featureplacement:
            if feature['name'] == featuretype:
                cnt += 1
        if cnt > 0:
            print('Placed %i %s' % (cnt, featuretype))

    if myargs.grassmap:
        grassmap = Image.open(myargs.grassmap)
        if grassmap.size != (mapx / 4, mapy / 4):
            print(
                'Error: Incorrect %s grassmap dimensions of (%ix%i), image size should be exactly %ix%i for a spring map size of (%ix%i)' % (
                    myargs.grassmap, grassmap.size[0], grassmap.size[1], mapx / 4, mapy / 4, springmapx, springmapy))
            return
        grassmap_pixels = grassmap.load()
        for row in range(grassmap.size[1]):
            for col in range(grassmap.size[0]):
                if sum(grassmap_pixels[col, row]) != 0:
                    vegmap[(mapx / 4) * row + col] = 1
    print('Total grass coverage of map is %f percent' % (100.0 * sum(vegmap) / float(mapx * mapy / 16)))
    # actually load the texture image:

    # draw geovent onto texture
    if myargs.geoventfile:
        geoventimg = Image.open(myargs.geoventfile)
        geoventimg_pixels = geoventimg.load()
        if sum(geoventimg.size) > 1000:
            print(
                'Warning: You have specified a very large %s geo vent file, are you sure this is what you desire?' % (
                    str(geoventimg.size)))
        for feature in featureplacement:
            if feature['name'].lower() == 'geovent':
                geovent_pixel_y = 0
                for row in range(feature['z'] - geoventimg.size[1] / 2, feature['z'] + geoventimg.size[1] / 2, 1):
                    geovent_pixel_y += 1
                    geovent_pixel_x = 0
                    for col in range(feature['x'] - geoventimg.size[0] / 2, feature['z'] + geoventimg.size[0] / 2, 1):
                        geovent_pixel_x += 1
                        if sum(geoventimg_pixels[geovent_pixel_x, geovent_pixel_y]) != 3 * 255:
                            try:
                                intex_pixels[col, row] = geoventimg_pixels[geovent_pixel_x, geovent_pixel_y]
                            except IndexError:
                                print(
                                    'Warning: Failed to draw a geovent image pixel onto the main texture at %ix%i' % (
                                        col, row))

    typemap = [0] * (mapx / 2) * (mapy / 2)
    if myargs.typemap:
        print('Loading typemap', myargs.typemap)
        typemap_img = Image.open(myargs.typemap)
        if typemap_img.size != (mapx / 2, mapy / 2):
            print(
                'Error: Incorrect %s typemap dimensions of (%ix%i), image size should be exactly %ix%i for a spring map size of (%ix%i)' % (
                    myargs.typemap, typemap_img.size[0], typemap_img.size[1], mapx / 2, mapy / 2, springmapx,
                    springmapy))
            return

        typemap_img_pixel = typemap_img.load()
        for row in range(typemap_img.size[1]):
            for col in range(typemap_img.size[0]):
                typemap[(mapx / 2) * row + col] = typemap_img_pixel[col, row][0]

    # make 1024x1024 tiles for nvdxt:
    try:
        print('Creating temp directory for intermediate tiles')
        if not os.path.exists('temp'):
            os.makedirs('temp')
    except:
        print('Failed to create temp directory!')
        pass
    # todo: handle alpha in intex properly!
    print('Writing tiles')
    extension = 'bmp'
    if intex.mode == 'RGBA':
        extension = 'tiff'
    for tilex in range(springmapx / 2):
        for tiley in range(springmapx / 2):
            tileindex = tiley * (springmapx / 2) + tilex
            newtile = intex.crop((1024 * tilex, 1024 * tiley, 1024 * (tilex + 1), 1024 * (
                    tiley + 1)))  # The box is a 4-tuple defining the left, upper, right, and lower pixel coordinate.
            print(tileindex)
            newtile.save(os.path.join('temp', 'temp%i.%s' % (tileindex, extension)))
    print('')

    print('Converting to dds', end=' ')
    if myargs.linux:
        basecmd = 'convert -format dds -define dds:mipmaps=3 -define dds:compression=dxt1 temp/temp%i.%s temp/temp%i.dds'
        print('with the base command of:', basecmd)
        for tilex in range(springmapx / 2):
            for tiley in range(springmapx / 2):
                tileindex = tiley * (springmapx / 2) + tilex
                cmd = basecmd % (tileindex, extension, tileindex)
                os.system(cmd)
                print(tileindex)
        print('')
    else:
        compressionmethod = 'dxt1c'
        if intex.mode == 'RGBA':
            compressionmethod = 'dxt1a'
        cmd = 'nvdxt.exe -file temp\\temp*.%s -%s -outsamedir -nmips 4 %s' % (
            extension, compressionmethod, myargs.nvdxt_options)
        print('with the command: ', cmd)
        os.system(cmd)

    def ReadTile(xpos, ypos, sourcebuf):  # xpos and ypos aremultiples of 32
        outtile = b''
        sourceoffset = 0
        for i in range(4):  # main + 3 mips
            div = 1 << i
            xp = 8 / div
            yp = 8 / div
            for y in range(yp):
                for x in range(xp):
                    ptr = ((x + xpos / div / 4) + ((y + ypos / div / 4)) * (256 / (div))) * 8 + sourceoffset
                    outtile += sourcebuf[ptr:ptr + 8]
            sourceoffset += 524288 / (1 << (i * 2))
        return outtile

    minimapfilename = os.path.join('temp', 'mini.png')
    print('Creating minimap', minimapfilename, 'using the command:', end=' ')
    if myargs.minimap:
        minimapfilename = myargs.minimap
    else:
        mini = intex.resize((1024, 1024), Image.ANTIALIAS)
        mini.save(minimapfilename)
    if myargs.linux:
        cmd = 'convert -format dds -define dds:mipmaps=8 -define dds:compression=dxt1 %s temp/mini.dds' % (
            minimapfilename)
        print(cmd)
        os.system(cmd)
    else:
        cmd = 'nvdxt.exe -file %s -dxt1c -nmips 9 -output temp/mini.dds -Sinc -quality_highest' % (minimapfilename)
        print(cmd)
        os.system(cmd)
    minimapdata = open(os.path.join('temp', 'mini.dds'), 'rb').read()[128:]

    intex = None
    gc.collect()

    print('Building tiles')
    tilehash = {}  # yes, we are gonna use the tiles as keys to perform rapid lossless compresssion :D
    tileindices = {}

    for tilex in range(springmapx / 2):
        for tiley in range(springmapx / 2):
            tileindex = tiley * (springmapx / 2) + tilex
            ddsfile = open(os.path.join('temp', 'temp%i.dds' % (tileindex)), 'rb')
            ddsdata = ddsfile.read()[128:]
            for x in range(32):
                for y in range(32):
                    tile = ReadTile(32 * x, 32 * y, ddsdata)
                    if len(tile) != SMALL_TILE_SIZE:
                        raise Exception

                    if tile not in tilehash:
                        tilehash[tile] = len(tilehash)
                    tilepos = 32 * tilex + x + (32 * springmapx / 2) * (32 * tiley + y)
                    if tilepos in tileindices:
                        print('something is very wrong here with tilepos, aborting compilation')
                        print(x, y, tilex, tiley, tileindex)
                        return
                    tileindices[tilepos] = tilehash[tile]
            ddsfile.close()
    # TODO: tilehash is larger than max tiles sometimes!
    print('Lossless compression of 32x32 tiles: %i tiles used of %i maximum' % (
        len(tilehash), 256 * springmapx * springmapy))

    smtfilename = myargs.outfile.replace('.smf', '.smt')
    print('Writing tile file ', smtfilename)
    tilefile = open(smtfilename, 'wb')
    tilefile.write(TileFileHeader_struct.pack('spring tilefile\0', 1, len(tilehash), 32, 1))
    inversetiledict = {}
    for tile, index in tilehash.items():
        inversetiledict[index] = tile
    for i in range(len(inversetiledict)):
        tilefile.write(inversetiledict[i])
    tilefile.close()

    smffile = open(myargs.outfile, 'wb')
    # smffile.write(SMFHeader_struct.pack())
    # SMFHeader_struct
    magic = 'spring map file\0'
    version = 1
    mapid = random.randint(0, 31 ** 2)
    squaresize = 8
    texelspersquare = 8
    tilesize = 32

    numExtraHeaders = 1
    vegmapPtr = SMFHeader_struct.size + ExtraHeader_struct.size
    heightmapptr = vegmapPtr + mapx * mapy / 16
    typemapptr = heightmapptr + (2 * (mapx + 1) * (mapy + 1))
    minimapptr = typemapptr + mapx * mapy / 4
    metalmapptr = minimapptr + MINIMAP_SIZE

    tilesptr = metalmapptr + mapx * mapy / 4
    numtilefiles = 1
    numtiles = len(tilehash)

    # numtilefiles,numtiles, numtiles, smtfilename,\0,
    featureptr = tilesptr + 4 + 4 + 4 + len(smtfilename) + 1 + 4 * (mapx * mapy / 16)
    numfeaturetype = len(featuretypes)
    numfeatures = len(featureplacement)
    smffile.write(SMFHeader_struct.pack(magic, version, mapid, mapx, mapy, squaresize, texelspersquare, tilesize,
                                        myargs.minheight, myargs.maxheight, heightmapptr, typemapptr, tilesptr,
                                        minimapptr, metalmapptr, featureptr, numExtraHeaders))
    smffile.write(ExtraHeader_struct.pack(12, 1, vegmapPtr))
    print('Size of vegetation (grass) map in pixels = ', len(vegmap))
    for v in vegmap:
        smffile.write(struct.pack('B', v))
    for h in heights:
        smffile.write(struct.pack('<H', h))
    for t in typemap:
        smffile.write(struct.pack('B', t))
    smffile.write(minimapdata)
    if verbose:
        print('Length of minimap data chunk = ', len(minimapdata), ', should be equal to', MINIMAP_SIZE)
        print('Length of metalmap data chunk', len(metalmap))
    for m in metalmap:
        smffile.write(struct.pack('B', m))
    smffile.write(MapTileHeader_struct.pack(numtilefiles, numtiles))
    smffile.write(struct.pack('< i %is' % (len(smtfilename + '\0')), numtiles, smtfilename + '\0'))
    for i in range(mapx * mapy / 16):
        smffile.write(struct.pack('< i', tileindices[i]))
    smffile.write(MapFeatureHeader_struct.pack(numfeaturetype, numfeatures))
    for fname in featuretypes:
        smffile.write(struct.pack('%is' % (len(fname + '\0')), fname + '\0'))
    for f in featureplacement:
        smffile.write(
            MapFeatureStruct_struct.pack(featuretypes.index(f['name']), f['x'], f['y'], f['z'], f['rot'], f['scale']))
    smffile.close()

    print('Cleaning up temp dir...')
    if myargs.linux:
        # os.system('rm -r ./temp')
        pass
    else:
        # os.system('del /Q temp')
        pass
    print('All Done! You may now close the main window to exit the program :)')


class SMFMapDecompiler:
    def __init__(self, filename):
        verbose = True
        self.filename = filename
        self.basename = filename.rpartition('.')[0]
        self.smffile = open(filename, 'rb').read()
        self.SMFHeader = SMFHeader_struct.unpack_from(self.smffile, 0)

        self.magic = self.SMFHeader[0]  # ;      ///< "spring map file\0"
        self.version = self.SMFHeader[1]  # ;         ///< Must be 1 for now
        self.mapid = self.SMFHeader[
            2]  # ;           ///< Sort of a GUID of the file, just set to a random value when writing a map

        self.mapx = self.SMFHeader[3]  # ;            ///< Must be divisible by 128
        self.mapy = self.SMFHeader[4]  # ;            ///< Must be divisible by 128
        self.squareSize = self.SMFHeader[5]  # ;      ///< Distance between vertices. Must be 8
        self.texelPerSquare = self.SMFHeader[6]  # ;  ///< Number of texels per square, must be 8 for now
        self.tilesize = self.SMFHeader[7]  # ;        ///< Number of texels in a tile, must be 32 for now
        self.minHeight = self.SMFHeader[8]  # ;     ///< Height value that 0 in the heightmap corresponds to
        self.maxHeight = self.SMFHeader[9]  # ;     ///< Height value that 0xffff in the heightmap corresponds to

        self.heightmapPtr = self.SMFHeader[10]  # ;    ///< File offset to elevation data (short int[(mapy+1)*(mapx+1)])
        self.typeMapPtr = self.SMFHeader[11]  # ;      ///< File offset to typedata (unsigned char[mapy/2 * mapx/2])
        self.tilesPtr = self.SMFHeader[12]  # ;        ///< File offset to tile data (see MapTileHeader)
        self.minimapPtr = self.SMFHeader[
            13]  # ;      ///< File offset to minimap (always 1024*1024 dxt1 compresed data plus 8 mipmap sublevels)
        self.metalmapPtr = self.SMFHeader[14]  # ;     ///< File offset to metalmap (unsigned char[mapx/2 * mapy/2])
        self.featurePtr = self.SMFHeader[15]  # ;      ///< File offset to feature data (see MapFeatureHeader)

        self.numExtraHeaders = self.SMFHeader[16]  # ; ///< Numbers of extra headers following main header'''
        if verbose:
            attrs = vars(self)
            print(self.SMFHeader)

        print('Writing heightmap RAW (Remember, this is a %i by %i 16bit 1 channel IBM byte order raw!)' % (
            (1 + self.mapx), (1 + self.mapy)))
        self.heightmap = struct.unpack_from('< %iH' % ((1 + self.mapx) * (1 + self.mapy)), self.smffile,
                                            self.heightmapPtr)
        heightmap_file = open(self.basename + '_height.raw', 'wb')
        for pixel in self.heightmap:
            heightmap_file.write(struct.pack('< H', pixel))
        heightmap_file.close()

        print('Writing heightmap BMP')
        heightmap_img = Image.new('RGB', (1 + self.mapx, 1 + self.mapy), 'black')
        heightmap_img_pixels = heightmap_img.load()
        for x in range(heightmap_img.size[0]):
            for y in range(heightmap_img.size[1]):
                height = self.heightmap[(heightmap_img.size[0]) * y + x] / 256
                heightmap_img_pixels[x, y] = (height, height, height)
        heightmap_img.save(self.basename + '_height.bmp')

        print('Writing heightmap PNG')
        heightmap_png_file = open(self.basename + '_height.png', 'wb')
        heightmap_png_writer = png.Writer(width=1 + self.mapx, height=1 + self.mapy, greyscale=True, bitdepth=16)
        heightmap_per_rows = []
        for y in range(self.mapy + 1):
            heightmap_per_rows.append(self.heightmap[(self.mapx + 1) * y: (self.mapx + 1) * (y + 1)])
        heightmap_png_writer.write(heightmap_png_file, heightmap_per_rows)
        heightmap_png_file.close()

        print('Writing MetalMap')
        self.metalmap = struct.unpack_from('< %iB' % ((self.mapx / 2) * (self.mapy / 2)), self.smffile,
                                           self.metalmapPtr)
        metalmap_img = Image.new('RGB', (self.mapx / 2, self.mapy / 2), 'black')
        metalmap_img_pixels = metalmap_img.load()
        for x in range(metalmap_img.size[0]):
            for y in range(metalmap_img.size[1]):
                metal = self.metalmap[(metalmap_img.size[0]) * y + x]
                metalmap_img_pixels[x, y] = (metal, 0, 0)
        metalmap_img.save(self.basename + '_metal.bmp')

        print('Writing typemap')
        self.typemap = struct.unpack_from('< %iB' % ((self.mapx / 2) * (self.mapy / 2)), self.smffile, self.typeMapPtr)
        typemap_img = Image.new('RGB', (self.mapx / 2, self.mapy / 2), 'black')
        typemap_img_pixels = typemap_img.load()
        for x in range(typemap_img.size[0]):
            for y in range(typemap_img.size[1]):
                typep = self.typemap[(typemap_img.size[0]) * y + x]
                typemap_img_pixels[x, y] = (typep, 0, 0)
        typemap_img.save(self.basename + '_type.bmp')

        print('Writing minimap')
        miniddsheaderstr = ([68, 68, 83, 32, 124, 0, 0, 0, 7, 16, 10, 0, 0, 4, 0, 0, 0, 4, 0, 0, 0, 0, 8, 0, 0, 0, 0, 0,
                             11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 32, 0, 0, 0, 4, 0, 0, 0, 68, 88, 84, 49, 0, 0, 0, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 8, 16, 64, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.minimap = self.smffile[self.minimapPtr:self.minimapPtr + MINIMAP_SIZE]
        minimap_file = open(self.basename + '_mini.dds', 'wb')
        for c in miniddsheaderstr:
            minimap_file.write(struct.pack('< B', c))
        minimap_file.write(self.minimap)
        minimap_file.close()

        print('Writing grassmap')
        # vegmapoffset = SMFHeader_struct.size+ExtraHeader_struct.size+4
        for extraheader_index in range(self.numExtraHeaders):
            extraheader = ExtraHeader_struct.unpack_from(self.smffile,
                                                         extraheader_index * ExtraHeader_struct.size + SMFHeader_struct.size)
            if verbose:
                print('Extraheader:', extraheader, '(size, type, extraoffset)')
            extraheader_size, extraheader_type, extraoffset = extraheader
            # print('ExtraHeader',extraheader)
            if extraheader_type == 1:  # grass
                # self.grassmap=struct.unpack_from('< %iB'%((self.mapx/4)*(self.mapy/4)),self.smffile,ExtraHeader_struct.size+SMFHeader_struct.size+extraheader_size)
                self.grassmap = struct.unpack_from('< %iB' % ((self.mapx / 4) * (self.mapy / 4)), self.smffile,
                                                   extraoffset)
                grassmap_img = Image.new('RGB', (self.mapx / 4, self.mapy / 4), 'black')
                grassmap_img_pixels = grassmap_img.load()
                for x in range(grassmap_img.size[0]):
                    for y in range(grassmap_img.size[1]):
                        grass = self.grassmap[(grassmap_img.size[0]) * y + x]
                        if grass == 1:
                            grass = 255
                        else:
                            grass = 0
                        grassmap_img_pixels[x, y] = (grass, grass, grass)
                grassmap_img.save(self.basename + '_grass.bmp')

        # MapFeatureHeader is followed by numFeatureType zero terminated strings indicating the names
        # of the features in the map. Then follow numFeatures MapFeatureStructs.
        self.mapfeaturesheader = MapFeatureHeader_struct.unpack_from(self.smffile, self.featurePtr)
        if verbose:
            print('MapFeatureHeader=', self.mapfeaturesheader, '(numFeatureType, numFeatures)')
            print('MapTileHeader=', MapTileHeader_struct.unpack_from(self.smffile,
                                                                     self.tilesPtr), '(numTileFiles, numTiles)')
            self.somelulz = self.smffile[self.tilesPtr - 10:self.tilesPtr + 30]
        self.numFeatureType, self.numFeatures = self.mapfeaturesheader
        self.featurenames = []
        featureoffset = self.featurePtr + MapFeatureHeader_struct.size
        while len(self.featurenames) < self.numFeatureType:
            featurename = unpack_null_terminated_string(self.smffile, featureoffset)
            self.featurenames.append(featurename)
            featureoffset += len(featurename) + 1  # cause of null terminator
            print(featurename)

            '''
            nextchar= 'N'
            while nextchar != '\0':
                nextchar=struct.unpack_from('c',self.smffile,len(featurename)+self.featurePtr+MapFeatureHeader_struct.size
                    +sum([len(fname)+1 for fname in self.featurenames]))[0]
                if nextchar =='\0':
                    self.featurenames.append(featurename)
                    featurename=''
                else:
                    featurename+=nextchar
            '''

        print('Features found in map definition', self.featurenames)
        feature_offset = self.featurePtr + MapFeatureHeader_struct.size + sum(
            [len(fname) + 1 for fname in self.featurenames])
        self.features = []
        for feature_index in range(self.numFeatures):
            feat = MapFeatureStruct_struct.unpack_from(self.smffile,
                                                       feature_offset + MapFeatureStruct_struct.size * feature_index)
            # print(feat)
            self.features.append(
                {'name': self.featurenames[feat[0]], 'x': feat[1], 'y': feat[2], 'z': feat[3], 'rotation': feat[4],
                 'relativeSize': feat[5], })
        # print(self.features[-1])
        print('Writing feature placement file')
        feature_file = open(self.basename + '_featureplacement.lua', 'w')
        for feature in self.features:
            feature_file.write('{ name = \'%s\', x = %i, z = %i, rot = "%i" ,scale = %f },\n' % (
                feature['name'], feature['x'], feature['z'], feature['rotation'], feature['relativeSize']))
        feature_file.close()

        print('loading tile files')
        self.maptileheader = MapTileHeader_struct.unpack_from(self.smffile, self.tilesPtr)
        self.numtilefiles, self.numtiles = self.maptileheader
        self.tilefiles = []
        tileoffset = self.tilesPtr + MapTileHeader_struct.size
        for i in range(self.numtilefiles):
            numtilesinfile = struct.unpack_from('< i', self.smffile, tileoffset)[0]
            tileoffset += 4  # sizeof(int)
            tilefilename = unpack_null_terminated_string(self.smffile, tileoffset)
            tileoffset += len(tilefilename) + 1  # cause of null terminator
            self.tilefiles.append(
                # [tilefilename, numtilesinfile, open(filename.rpartition('\\')[0] + '\\' + tilefilename, 'rb').read()])
                [tilefilename, numtilesinfile, open(tilefilename, 'rb').read()])
            print(tilefilename, 'has', numtilesinfile, 'tiles')
        self.tileindices = struct.unpack_from('< %ii' % ((self.mapx / 4) * (self.mapy / 4)), self.smffile, tileoffset)

        self.tiles = []
        for tilefile in self.tilefiles:
            tileFileHeader = TileFileHeader_struct.unpack_from(tilefile[2], 0)
            magic, version, numTiles, tileSize, compressionType = tileFileHeader
            # print(tilefile[0],': magic,version,numTiles,tileSize,compressionType',magic,version,numTiles,tileSize,compressionType)
            for i in range(numTiles):
                self.tiles.append(struct.unpack_from('< %is' % (SMALL_TILE_SIZE), tilefile[2],
                                                     TileFileHeader_struct.size + i * SMALL_TILE_SIZE)[0])

        print('Generating texture, this is very very slow (few minutes)')
        textureimage = Image.new('RGB', (self.mapx * 8, self.mapy * 8), 'black')
        textureimagepixels = textureimage.load()
        for ty in range(self.mapy / 4):
            # print('row',ty)
            for tx in range(self.mapx / 4):
                currtile = self.tiles[self.tileindices[(self.mapx / 4) * ty + tx]]
                # print('Tile',(self.mapx/4)*ty+tx)
                # one tile is 32x32, and pythonDecodeDXT1 will need one 'row' of data, assume this is 8*8 bytes
                for rows in range(8):
                    # print("currtile",currtile)
                    dxdata = currtile[rows * 64:(rows + 1) * 64]
                    # print(len(dxdata),dxdata)
                    dxtrows = pythonDecodeDXT1(dxdata)  # decode in 8 block chunks
                    for x in range(tx * 32, (tx + 1) * 32):
                        for y in range(ty * 32 + 4 * rows, ty * 32 + 4 + 4 * rows):
                            # print(rows, tx,ty,x,y)
                            # print(dxtrows)
                            oy = (ty * 32 + 4 * rows)
                            textureimagepixels[x, y] = (
                                ord(dxtrows[y - oy][3 * (x - tx * 32) + 0]),
                                ord(dxtrows[y - oy][3 * (x - tx * 32) + 1]),
                                ord(dxtrows[y - oy][3 * (x - tx * 32) + 2]))
        textureimage.save(self.basename + '_texture.bmp')
        infofile = open(self.basename + '_compilation_settings.txt', 'w')

        infofile.write('-%s\n%s\n' % ('n', str(self.minHeight)))
        infofile.write('-%s\n%s\n' % ('x', str(self.maxHeight)))
        infofile.write('-%s\n%s\n' % ('o', self.basename + '_recompiled.smf'))
        infofile.write('-%s\n%s\n' % ('m', self.basename + '_metal.bmp'))
        infofile.write('-%s\n%s\n' % ('t', self.basename + '_texture.bmp'))
        infofile.write('-%s\n%s\n' % ('a', self.basename + '_height.png'))
        infofile.write('-%s\n%s\n' % ('g', ''))
        infofile.write('-%s\n%s\n' % ('y', self.basename + '_type.bmp'))
        infofile.write('-%s\n%s\n' % ('r', self.basename + '_grass.bmp'))
        infofile.write('-%s\n%s\n' % ('k', self.basename + '_featureplacement.lua'))

        infofile.close()

        print('Done, one final bit of important info: the maps maxheight is %i, while the minheight is %i' % (
            self.maxHeight, self.minHeight))