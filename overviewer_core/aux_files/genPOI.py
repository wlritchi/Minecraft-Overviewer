#!/usr/bin/python2

'''
genPOI.py

Scans regionsets for TileEntities and Entities, filters them, and writes out
POI/marker info.

A markerSet is list of POIs to display on a tileset.  It has a display name,
and a group name.

markersDB.js holds a list of POIs in each group
markers.js holds a list of which markerSets are attached to each tileSet


'''
import os
import logging
import json
import sys
import re
import urllib2

from itertools import chain
from optparse import OptionParser

from overviewer_core import logger
from overviewer_core import nbt
from overviewer_core import configParser, world

UUID_LOOKUP_URL = 'https://sessionserver.mojang.com/session/minecraft/profile/'

def get_internal_name(name, filt, rset):
    "Generates a unique name for a markerset. Should not be user visible."
    return sanitize(name) + hex(hash(filt))[-4:] + '_' + hex(hash(rset))[-4:]

def sanitize(name):
    "Replaces bad characters with good characters."
    for char in [' ', '(', ')']:
        name = name.replace(char, '_')
    return name

def iter_entities(rset):
    "Iterates over the entities and tile entities in the given regionset."
    for (x, z, _) in rset.iterate_chunks():
        data = rset.get_chunk(x, z)
        try:
            for d in data['TileEntities']:
                yield d
            for d in data['Entities']:
                yield d
        except nbt.CorruptChunkError:
            logging.warning("Ignoring POIs in corrupt chunk %d,%d", x, z)

def iter_player_pois(rset, worldpath):
    "Iterates over the players in the given regionset."
    dimension = None
    try:
        dimension = { None      : 0
                    , 'DIM-1'   : -1
                    , 'DIM1'    : 1
                    }[rset.get_type()]
    except KeyError, e:
        mystdim = re.match(r"^DIM_MYST(\d+)$", e.message)  # Dirty hack. Woo!
        if mystdim:
            dimension = int(mystdim.group(1))
        else:
            raise
    playerdir = os.path.join(worldpath, "playerdata")
    useUUIDs = True
    if not os.path.isdir(playerdir):
        playerdir = os.path.join(worldpath, "players")
        useUUIDs = False

    if os.path.isdir(playerdir):
        playerfiles = os.listdir(playerdir)
        playerfiles = [x for x in playerfiles if x.endswith(".dat")]
        isSinglePlayer = False

    else:
        playerfiles = [os.path.join(worldpath, "level.dat")]
        isSinglePlayer = True

    for playerfile in playerfiles:
        try:
            data = nbt.load(os.path.join(playerdir, playerfile))[1]
            if isSinglePlayer:
                data = data['Data']['Player']
        except IOError:
            logging.warning("Skipping bad player dat file %r", playerfile)
            continue
        playername = playerfile.split(".")[0]
        if useUUIDs:
            try:
                lookup_url = UUID_LOOKUP_URL + playername.replace('-','')
                profile = json.loads(urllib2.urlopen(lookup_url).read())
                if 'name' in profile:
                    playername = profile['name']
            except ValueError:
                logging.warning("Unable to get name for UUID %s", playername)
        if isSinglePlayer:
            playername = 'Player'
        if data['Dimension'] == dimension:
            # Position at last logout
            data['id'] = "Player"
            data['EntityId'] = playername
            data['x'] = int(data['Pos'][0])
            data['y'] = int(data['Pos'][1])
            data['z'] = int(data['Pos'][2])
            yield data
        if "SpawnX" in data and dimension == 0:
            # Spawn position (bed or main spawn)
            spawn = {"id": "PlayerSpawn",
                     "EntityId": playername,
                     "x": data['SpawnX'],
                     "y": data['SpawnY'],
                     "z": data['SpawnZ']}
            yield spawn

def main():
    if os.path.basename(sys.argv[0]) == """genPOI.py""":
        helptext = """genPOI.py
            %prog --config=<config file> [--quiet]"""
    else:
        helptext = """genPOI
            %prog --genpoi --config=<config file> [--quiet]"""

    logger.configure()

    parser = OptionParser(usage=helptext)
    parser.add_option( "-c"
                     , "--config"
                     , dest   = "config"
                     , action = "store"
                     , help   = "Specify the config file to use."
                     )
    parser.add_option( "--quiet"
                     , dest   = "quiet"
                     , action = "count"
                     , help   = "Reduce logging output"
                     )
    parser.add_option( "--skip-scan"
                     , dest   = "skipscan"
                     , action = "store_true"
                     , help   = "Skip scanning for entities when using GenPOI"
                     )

    (options, _) = parser.parse_args()
    if not options.config:
        parser.print_help()
        return

    if options.quiet > 0:
        logger.configure(logging.WARN, False)

    # Parse the config file
    mw_parser = configParser.MultiWorldParser()
    mw_parser.parse(options.config)
    try:
        config = mw_parser.get_validated_config()
    except Exception:
        logging.exception("An error was encountered with your configuration. See the info below.")
        return 1

    destdir = config['outputdir']
    # saves us from creating the same World object over and over again
    worldcache = {}

    markersets_by_rset = {}
    worldpaths = {}

    marker_db = {}
    markers = {}

    for rname, render in config['renders'].iteritems():
        try:
            worldpath = config['worlds'][render['world']]
        except KeyError:
            logging.error( "Render %s's world is '%s', but I could not find a "
                         + "corresponding entry in the worlds dictionary."
                         , rname
                         , render['world']
                         )
            return 1
        render['worldname_orig'] = render['world']
        render['world'] = worldpath

        # find or create the world object
        if (render['world'] not in worldcache):
            w = world.World(render['world'])
            worldcache[render['world']] = w
        else:
            w = worldcache[render['world']]

        rset = w.get_regionset(render['dimension'][1])
        if rset == None: # indicates no such dimension was found:
            logging.error( "Sorry, you requested dimension '%s' for the render "
                         + "'%s', but I couldn't find it"
                         , render['dimension'][0]
                         , rname
                         )
            return 1

        markers[rname] = []
        for f in render['markers']:
            filter_function = f['filterFunction']
            internal_name = get_internal_name(f['name'], filter_function, rset)
            markers[rname].append({
                'groupName'         : internal_name,
                'displayName'       : f['name'],
                'icon'              : f.get('icon', 'signpost_icon.png'),
                'createInfoWindow'  : f.get('createInfoWindow',True),
                'checked'           : f.get('checked', False),
            })
            marker_db[internal_name] = {
                'created'   : False,
                'name'      : f['name'],
                'raw'       : filter(lambda x: x is not None, (
                                handle_poi_result(poi, filter_function(poi))
                                    for poi in render['manualpois']
                            ))
            }
            if rset not in markersets_by_rset:
                markersets_by_rset[rset] = []
                worldpaths[rset] = worldpath
            markersets_by_rset[rset].append((internal_name, filter_function))

    for (regionset, markersets) in markersets_by_rset.iteritems():
        pois = iter_player_pois(regionset, worldpaths[rset])
        if not options.skipscan:
            pois = chain(iter_entities(regionset), pois)

        count = 0
        freq = 100
        for poi in pois:
            for (internal_name, filter_function) in markersets:
                poi_result = handle_poi_result(poi, filter_function(poi))
                if poi_result is not None:
                    marker_db[internal_name]['raw'].append(poi_result)

            # report 100, 200, ..., 1000, 2000, ..., 10000, 20000, ...
            count += 1
            if count % freq == 0:
                logging.info("Processed %d POIs so far in regionset.", count)
            if count == freq * 10:
                freq = count

    with open(os.path.join(destdir, "markersDB.js"), "w") as output:
        output.write("var markersDB=")
        json.dump(marker_db, output, indent=2)
        output.write(";\n")
    with open(os.path.join(destdir, "markers.js"), "w") as output:
        output.write("var markers=")
        json.dump(markers, output, indent=2)
        output.write(";\n")
    with open(os.path.join(destdir, "baseMarkers.js"), "w") as output:
        output.write("overviewer.util.injectMarkerScript('markersDB.js');\n")
        output.write("overviewer.util.injectMarkerScript('markers.js');\n")
        output.write("overviewer.collections.haveSigns=true;\n")
    logging.info("Done")

def handle_poi_result(poi, filter_result):
    "Processes POIs and filter outputs into the markersDB format."
    if filter_result:
        d = {
            'x' : poi['x'] if 'x' in poi else poi['Pos'][0],
            'y' : poi['y'] if 'y' in poi else poi['Pos'][1],
            'z' : poi['z'] if 'z' in poi else poi['Pos'][2]
        }
        if isinstance(filter_result, basestring):
            d['text'] = d['hovertext'] = filter_result
        elif type(filter_result) == tuple:
            (d['hovertext'], d['text']) = filter_result
        # Dict support to allow more flexible things in the future, polylines, etc.
        elif type(filter_result) == dict:
            d['text'] = filter_result['text']
            # Use custom hovertext if provided...
            if 'hovertext' in filter_result and isinstance(filter_result['hovertext'], basestring):
                d['hovertext'] = filter_result['hovertext']
            else: # ...otherwise default to display text.
                d['hovertext'] = filter_result['text']
            if 'polyline' in filter_result and type(filter_result['polyline']) == tuple:
                d['polyline'] = [ { 'x' : point['x']
                                  , 'y' : point['y']
                                  , 'z' : point['z']
                                  }
                                    for point in filter_result['polyline']
                                ]
                if isinstance(filter_result['color'], basestring):
                    d['strokeColor'] = filter_result['color']
        if 'icon' in filter_result:
            d['icon'] = filter_result['icon']
        elif 'icon' in poi:
            d['icon'] = poi['icon']
        if 'createInfoWindow' in poi:
            d['createInfoWindow'] = poi['createInfoWindow']
        return d

if __name__ == "__main__":
    main()
