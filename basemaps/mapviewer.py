# -*- coding:utf-8 -*-

#  ***** GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#  All rights reserved.
#  ***** GPL LICENSE BLOCK *****

#built-in imports
import math
import os
import io
import threading
import datetime
import sqlite3
import urllib.request
import imghdr

#bpy imports
import bpy
from bpy.props import StringProperty, IntProperty, FloatProperty, BoolProperty, EnumProperty, FloatVectorProperty
from bpy_extras.view3d_utils import region_2d_to_location_3d, region_2d_to_vector_3d
import blf, bgl

#deps imports
from PIL import Image
try:
	from osgeo import osr
except:
	PROJ = False
else:
	PROJ = True

#addon import
from .servicesDefs import grids, sources


####################################

#http://www.geopackage.org/spec/#tiles
#https://github.com/GitHubRGI/geopackage-python/blob/master/Packaging/tiles2gpkg_parallel.py
#https://github.com/Esri/raster2gpkg/blob/master/raster2gpkg.py


#table_name refer to the name of the table witch contains tiles data
#here for simplification, table_name will always be named "gpkg_tiles"

class GeoPackage():

	MAX_DAYS = 90

	def __init__(self, path, tm):
		self.dbPath = path
		self.name = os.path.splitext(os.path.basename(path))[0]
		
		#Get props from TileMatrix object
		self.crs = tm.CRS
		self.tileSize = tm.tileSize
		self.xmin, self.ymin, self.xmax, self.ymax = tm.globalbbox
		self.resolutions = tm.getResList()

		if not self.isGPKG():
			self.create()
			self.insertMetadata()
			
			self.insertCRS(self.crs, str(self.crs), wkt='')
			#self.insertCRS(3857, "Web Mercator", wkt='')
			#self.insertCRS(4326, "WGS84", wkt='')

			self.insertTileMatrixSet()


	def isGPKG(self):
		if not os.path.exists(self.dbPath):
			return False	
		db = sqlite3.connect(self.dbPath)
		
		#check application id
		app_id = db.execute("PRAGMA application_id").fetchone()
		if not app_id[0] == 1196437808:
			db.close()
			return False
			
		#quick check of table schema
		try:
			db.execute('SELECT table_name FROM gpkg_contents LIMIT 1')
			db.execute('SELECT srs_name FROM gpkg_spatial_ref_sys LIMIT 1')
			db.execute('SELECT table_name FROM gpkg_tile_matrix_set LIMIT 1')
			db.execute('SELECT table_name FROM gpkg_tile_matrix LIMIT 1')
			db.execute('SELECT zoom_level, tile_column, tile_row, tile_data FROM gpkg_tiles LIMIT 1')
		except:
			db.close()
			return False
		else:
			db.close()
			return True  
		
		
	def create(self):
		"""Create default geopackage schema on the database."""
		db = sqlite3.connect(self.dbPath) #this attempt will create a new file if not exist
		cursor = db.cursor()

		# Add GeoPackage version 1.0 ("GP10" in ASCII) to the Sqlite header
		cursor.execute("PRAGMA application_id = 1196437808;")
		
		cursor.execute("""
			CREATE TABLE gpkg_contents (
				table_name TEXT NOT NULL PRIMARY KEY,
				data_type TEXT NOT NULL,
				identifier TEXT UNIQUE,
				description TEXT DEFAULT '',
				last_change DATETIME NOT NULL DEFAULT
				(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
				min_x DOUBLE,
				min_y DOUBLE,
				max_x DOUBLE,
				max_y DOUBLE,
				srs_id INTEGER,
				CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
					REFERENCES gpkg_spatial_ref_sys(srs_id));
		""")
		
		cursor.execute("""
			CREATE TABLE gpkg_spatial_ref_sys (
				srs_name TEXT NOT NULL,
				srs_id INTEGER NOT NULL PRIMARY KEY,
				organization TEXT NOT NULL,
				organization_coordsys_id INTEGER NOT NULL,
				definition TEXT NOT NULL,
				description TEXT);
		""")

		cursor.execute("""
			CREATE TABLE gpkg_tile_matrix_set (
				table_name TEXT NOT NULL PRIMARY KEY,
				srs_id INTEGER NOT NULL,
				min_x DOUBLE NOT NULL,
				min_y DOUBLE NOT NULL,
				max_x DOUBLE NOT NULL,
				max_y DOUBLE NOT NULL,
				CONSTRAINT fk_gtms_table_name FOREIGN KEY (table_name)
					REFERENCES gpkg_contents(table_name),
				CONSTRAINT fk_gtms_srs FOREIGN KEY (srs_id)
					REFERENCES gpkg_spatial_ref_sys(srs_id));
		""")

		cursor.execute("""
			CREATE TABLE gpkg_tile_matrix (
				table_name TEXT NOT NULL,
				zoom_level INTEGER NOT NULL,
				matrix_width INTEGER NOT NULL,
				matrix_height INTEGER NOT NULL,
				tile_width INTEGER NOT NULL,
				tile_height INTEGER NOT NULL,
				pixel_x_size DOUBLE NOT NULL,
				pixel_y_size DOUBLE NOT NULL,
				CONSTRAINT pk_ttm PRIMARY KEY (table_name, zoom_level),
				CONSTRAINT fk_ttm_table_name FOREIGN KEY (table_name)
					REFERENCES gpkg_contents(table_name));
		""")		
		
		cursor.execute("""
			CREATE TABLE gpkg_tiles (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				zoom_level INTEGER NOT NULL,
				tile_column INTEGER NOT NULL,
				tile_row INTEGER NOT NULL,
				tile_data BLOB NOT NULL,
				last_modified TIMESTAMP DEFAULT (datetime('now','localtime')),
				UNIQUE (zoom_level, tile_column, tile_row));
		""")



	def insertMetadata(self):
		db = sqlite3.connect(self.dbPath)
		query = """INSERT INTO gpkg_contents (
					table_name, data_type,
					identifier, description,
					min_x, min_y, max_x, max_y,
					srs_id)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);"""
		db.execute(query, ("gpkg_tiles", "tiles", self.name, "Created with BlenderGIS", self.xmin, self.ymin, self.xmax, self.ymax, self.crs))  
		db.commit()
		db.close()		


	def insertCRS(self, code, name, wkt=''):
		db = sqlite3.connect(self.dbPath)
		db.execute(""" INSERT INTO gpkg_spatial_ref_sys (
					srs_id,
					organization,
					organization_coordsys_id,
					srs_name,
					definition)
				VALUES (?, ?, ?, ?, ?)
			""", (code, "EPSG", code, name, wkt))
		db.commit()
		db.close()


	def insertTileMatrixSet(self):
		db = sqlite3.connect(self.dbPath)
		
		#Tile matrix set
		query = """INSERT OR REPLACE INTO gpkg_tile_matrix_set (
					table_name, srs_id,
					min_x, min_y, max_x, max_y)
				VALUES (?, ?, ?, ?, ?, ?);"""
		db.execute(query, ('gpkg_tiles', self.crs, self.xmin, self.ymin, self.xmax, self.ymax))
		
		
		#Tile matrix of each levels
		for level, res in enumerate(self.resolutions):
			
			w = math.ceil( (self.xmax - self.xmin) / (self.tileSize * res) )
			h = math.ceil( (self.ymax - self.ymin) / (self.tileSize * res) )			
			
			query = """INSERT OR REPLACE INTO gpkg_tile_matrix (
						table_name, zoom_level,
						matrix_width, matrix_height,
						tile_width, tile_height,
						pixel_x_size, pixel_y_size)
					VALUES (?, ?, ?, ?, ?, ?, ?, ?);"""  
			db.execute(query, ('gpkg_tiles', level, w, h, self.tileSize, self.tileSize, res, res))	   
		
		
		db.commit()
		db.close()  
		

	def putTile(self, x, y, z, data):
		db = sqlite3.connect(self.dbPath)
		query = """INSERT OR REPLACE INTO gpkg_tiles 
		(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)"""
		db.execute(query, (z, x, y, data))
		db.commit()
		db.close()

	def getTile(self, x, y, z):
		#connect with detect_types parameter for automatically convert date to Python object
		db = sqlite3.connect(self.dbPath, detect_types=sqlite3.PARSE_DECLTYPES)
		query = 'SELECT tile_data, last_modified FROM gpkg_tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?'
		result = db.execute(query, (z, x, y)).fetchone()
		db.close()
		if result is None:
			return None
		timeDelta = datetime.datetime.now() - result[1]
		if timeDelta.days > self.MAX_DAYS:
			return None
		return result[0]


	def getTiles(self, tiles, z): #work in progress
		
		n = len(tiles)
		xs, ys = zip(*tiles)
		
		lst = [z] + list(xs) + list(ys)
		
		db = sqlite3.connect(self.dbPath, detect_types=sqlite3.PARSE_DECLTYPES)
		query = "SELECT * FROM gpkg_tiles WHERE zoom_level = ? AND tile_column IN (" + ','.join('?'*n) + ") AND tile_row IN (" + ','.join('?'*n) + ")"
		
		result = db.execute(query, lst).fetchall()
		db.close()
		
		print(n, len(result))
		for r in result:
			id, z, c, r, data, t = r
			print(c, r)

####################################

class Ellps():
	"""ellipsoid"""
	def __init__(self, a, b):
		self.a =  a#equatorial radius in meters
		self.b =  b#polar radius in meters
		self.f = (self.a-self.b)/self.a#inverse flat
		self.perimeter = (2*math.pi*self.a)#perimeter at equator

GRS80 = Ellps(6378137, 6356752.314245)


def reproj(crs1, crs2, x1, y1):
	"""
	Reproject x1,y1 coords from crs1 to crs2 
	Actually support only lat long (decimel degrees) <--> web mercator
	Warning, latitudes 90° or -90° are outside web mercator bounds
	"""
	if crs1 == 4326 and crs2 == 3857:
		long, lat = x1, y1
		k = GRS80.perimeter/360
		x2 = long * k
		lat = math.log( math.tan((90 + lat) * math.pi / 360.0 )) / (math.pi / 180.0)
		y2 = lat * k
		return x2, y2
	elif crs1 == 3857 and crs2 == 4326:
		k = GRS80.perimeter/360
		long = x1 / k
		lat = y1 / k
		lat = 180 / math.pi * (2 * math.atan( math.exp( lat * math.pi / 180.0)) - math.pi / 2.0)
		return long, lat
	else:
		#need an external lib (pyproj or gdal osr) to support others crs
		if not PROJ:
			raise NotImplementedError
		else: #gdal osr
			prj1 = osr.SpatialReference()
			prj1.ImportFromEPSG(crs1)

			prj2 = osr.SpatialReference()
			prj2.ImportFromEPSG(crs2)

			transfo = osr.CoordinateTransformation(prj1, prj2)
			x2, y2, z2 = transfo.TransformPoint(x1, y1)
			return x2, y2


####################################

class TileMatrix():
	
	defaultNbLevels = 24
	
	def __init__(self, gridDef):

		#create class attributes from grid dictionnary
		for k, v in gridDef.items():
			setattr(self, k, v)
		
		#Convert bbox to grid crs is needed
		if self.bboxCRS != self.CRS:
			lonMin, latMin, lonMax, latMax = self.bbox
			self.xmin, self.ymax = self.geoToProj(lonMin, latMax)
			self.xmax, self.ymin = self.geoToProj(lonMax, latMin)
		else:
			self.xmin, self.xmax = self.bbox[0], self.bbox[2]
			self.ymin, self.ymax = self.bbox[1], self.bbox[3]

		#Get initial resolution
		if getattr(self, 'resolutions', None) is not None:
			pass
		else:
			if getattr(self, 'initRes', None) is not None:
				pass
			else:
				# at zoom level zero, 1 tile covers whole bounding box
				dx = abs(self.xmax - self.xmin)
				dy = abs(self.ymax - self.ymin)
				dst = max(dx, dy)
				self.initRes = dst / self.tileSize

		#
		if getattr(self, 'resolutions', None) is not None:
			self.nbLevels = len(self.resolutions)
		elif getattr(self, 'nbLevels', None) is not None:
			pass
		else:
			self.nbLevels = self.defaultNbLevels

		
		# Define tile matrix origin
		if self.originLoc == "NW":
			self.originx, self.originy = self.xmin, self.ymax
		elif self.originLoc == "SW":
			self.originx, self.originy = self.xmin, self.ymin
		else:
			raise NotImplementedError
	
	@property
	def globalbbox(self):
		return self.xmin, self.ymin, self.xmax, self.ymax


	def geoToProj(self, long, lat):
		"""convert longitude latitude un decimal degrees to grid crs"""
		if self.CRS == 4326:
			return long, lat
		else:
			return reproj(4326, self.CRS, long, lat)

	def projToGeo(self, x, y):
		"""convert grid crs coords to longitude latitude in decimal degrees"""
		if self.CRS == 4326:
			return x, y
		else:
			return reproj(self.CRS, 4326, x, y)


	def getResList(self):
		if getattr(self, 'resolutions', None) is not None:
			return self.resolutions
		else:
			return [self.initRes / self.resFactor**zoom for zoom in range(self.nbLevels)]

	def getRes(self, zoom):
		"""Resolution (meters/pixel) for given zoom level (measured at Equator)"""
		if getattr(self, 'resolutions', None) is not None:
			if zoom > len(self.resolutions):
				zoom = len(self.resolutions)
			return self.resolutions[zoom]
		else:
			return self.initRes / self.resFactor**zoom


	def getTileNumber(self, x, y, zoom):
		"""Convert projeted coords to tiles number"""
		res = self.getRes(zoom)
		geoTileSize = self.tileSize * res
		dx = x - self.originx
		if self.originLoc == "NW":
			dy = self.originy - y
		else:
			dy = y - self.originy
		col = dx / geoTileSize
		row = dy / geoTileSize
		col = int(math.floor(col))
		row = int(math.floor(row))
		return col, row

	def getTileCoords(self, col, row, zoom):
		"""
		Convert tiles number to projeted coords
		(top left pixel if matrix origin is NW)
		"""
		res = self.getRes(zoom)
		geoTileSize = self.tileSize * res
		x = self.originx + (col * geoTileSize)
		if self.originLoc == "NW":
			y = self.originy - (row * geoTileSize)
		else:
			y = self.originy + (row * geoTileSize) #bottom left
			y += geoTileSize #top left
		return x, y
	

###################"

class MapService():
	"""
	Represent a tile service from source
	""" 
	
	def __init__(self, srcKey, cacheFolder):
		

		#create class attributes from source dictionnary
		self.srcKey = srcKey
		source = sources[self.srcKey]
		for k, v in source.items():
			setattr(self, k, v)

		#Build objects from layers definitions
		class Layer(): pass
		layersObj = {}
		for layKey, layDict in self.layers.items(): 
			lay = Layer()
			for k, v in layDict.items():
				setattr(lay, k, v)
			layersObj[layKey] = lay
		self.layers = layersObj
	
		#Build source tile matrix
		self.tm1 = TileMatrix(grids[self.grid])
		
		#Build destination tile matrix (NOT YET IMPLEMENTED)
		if getattr(self, 'dstGrid', None) is not None:
			self.tm2 = TileMatrix(grids[self.tm2])
		else:
			self.tm2 = self.tm1

		#Init cache dict
		self.cacheFolder = cacheFolder
		self.caches = {}


	def getCache(self, layKey):
		'''Return existing cache for requested layer or built it if not exists'''
		cache = self.caches.get(layKey)
		if cache is None:			
			mapKey = self.srcKey + '_' + layKey
			dbPath = self.cacheFolder + mapKey+ ".gpkg"
			self.caches[layKey] = GeoPackage(dbPath, self.tm2)
			return self.caches[layKey]
		else:
			return cache



	def buildUrl(self, layKey, col, row, zoom):
		"""
		Receive tiles coords coords in destination tile matrix space
		convert to source tile matrix space and build request url
		"""
		url = self.urlTemplate
		lay = self.layers[layKey]
		
		if self.service == 'TMS':
			url = url.replace("{LAY}", lay.urlKey)
			if not self.quadTree:
				url = url.replace("{X}", str(col))
				url = url.replace("{Y}", str(row))
				url = url.replace("{Z}", str(zoom))
			else:
				quadkey = self.getQuadKey(col, row, zoom)
				url = url.replace("{QUADKEY}", quadkey) 
			
		if self.service == 'WMTS':
			url = self.urlTemplate['BASE_URL']
			if url[-1] != '?' :
				url += '?'
			params = ['='.join([k,v]) for k, v in self.urlTemplate.items() if k != 'BASE_URL']
			url += '&'.join(params)
			url = url.replace("{LAY}", lay.urlKey)
			url = url.replace("{FORMAT}", lay.format)
			url = url.replace("{STYLE}", lay.style)
			url = url.replace("{MATRIX}", self.matrix)  
			url = url.replace("{X}", str(col))
			url = url.replace("{Y}", str(row))
			url = url.replace("{Z}", str(zoom))
			
		if self.service == 'WMS':
			url = self.urlTemplate['BASE_URL']
			if url[-1] != '?' :
				url += '?'  
			params = ['='.join([k,v]) for k, v in self.urlTemplate.items() if k != 'BASE_URL']
			url += '&'.join(params)
			url = url.replace("{LAY}", lay.urlKey)
			url = url.replace("{FORMAT}", lay.format)
			url = url.replace("{STYLE}", lay.style)
			url = url.replace("{CRS}", str(self.tm1.CRS))
			url = url.replace("{WIDTH}", str(self.tileSize))
			url = url.replace("{HEIGHT}", str(self.tileSize))
			
			xmin, ymax = self.tm1.getTileCoords(col, row, zoom)
			xmax = xmin + self.tm1.tileSize * self.tm1.getRes(zoom)
			ymin = ymax - self.tm1.tileSize * self.tm1.getRes(zoom)
			if self.urlTemplate['VERSION'] == '1.3.0' and self.tm1.CRS == 4326:
				bbox = ','.join(map(str,[ymin,xmin,ymax,xmax]))
			else:
				bbox = ','.join(map(str,[xmin,ymin,xmax,ymax]))
			url = url.replace("{BBOX}", bbox)
													
		return url


	def getQuadKey(self, x, y, z):
		"Converts TMS tile coordinates to Microsoft QuadTree"
		quadKey = ""
		for i in range(z, 0, -1):
			digit = 0
			mask = 1 << (i-1)
			if (x & mask) != 0:
				digit += 1
			if (y & mask) != 0:
				digit += 2
			quadKey += str(digit)
		return quadKey


	def getTile(self, layKey, col, row, zoom):
		"""
		Return bytes data of requested tile
		Tile is downloaded from map service or directly pick up from cache database.
		"""

		cache = self.getCache(layKey)
	
		#don't try to get tiles out of map bounds
		x,y = self.tm2.getTileCoords(col, row, zoom) #top left
		if row < 0 or col < 0:
			return None
		elif not self.tm2.xmin <= x < self.tm2.xmax or not self.tm2.ymin < y <= self.tm2.ymax:
			return None
				
		#check if tile already exists in cache
		data = cache.getTile(col, row, zoom)
		
		#if so check if its a valid image
		if data is not None:
			format = imghdr.what(None, data)
			if format is None:#corrupted
				data = None
			
		#if not or corrupted try to download it from map service			
		if data is None:
		
			url = self.buildUrl(layKey, col, row, zoom)
			#print(url)
			
			try:
				#make request
				req = urllib.request.Request(url, None, self.headers)
				handle = urllib.request.urlopen(req, timeout=3)
				#open image stream
				data = handle.read()
				handle.close()
			except:
				print("Can't download tile x"+str(col)+" y"+str(row))
				print(url)
				data = None
		
			#Make sure the stream is correct and put in db
			if data is not None:
				format = imghdr.what(None, data)
				if format is None:
					data = None
				else:
					cache.putTile(col, row, self.zoom, data)
		
		return data


	def listTiles(self, bbox, zoom):
		
		xmin, ymin, xmax, ymax = bbox
				
		#Get first tile indices (tiles matrix origin is top left)
		firstCol, firstRow = self.tm1.getTileNumber(xmin, ymax, zoom)
		
		#Total number of tiles required
		nbTilesX = math.ceil( (xmax - xmin) / (self.tm1.tileSize * self.tm1.getRes(zoom)) )
		nbTilesY = math.ceil( (ymax - ymin) / (self.tm1.tileSize * self.tm1.getRes(zoom)) )
			
		#Add more tiles because background image will be offseted 
		# and could be to small to cover all area
		nbTilesX += 1
		nbTilesY += 1

		#Build list of required column and row numbers
		cols = [firstCol+i for i in range(nbTilesX)]
		if self.tm1.originLoc == "NW":
			rows = [firstRow+i for i in range(nbTilesY)]
		else:
			rows = [firstRow-i for i in range(nbTilesY)]

		return cols, rows


	def getTiles(self, layKey, tiles, z): #test
		cache = self.getCache(layKey)
		cache.getTiles(tiles, z)


####################

class MapImage(MapService):
	
	"""Handle a map as background image in Blender"""
	
	def __init__(self, context):

		#Get context
		self.scn = context.scene
		self.area = context.area
		self.area3d = [r for r in self.area.regions if r.type == 'WINDOW'][0]
		self.view3d = self.area.spaces.active
		self.reg3d = self.view3d.region_3d
		
		#Get tool props stored in scene
		folder = self.scn.cacheFolder
		mapKey = self.scn.mapSource
		self.srcKey, self.layKey = mapKey.split(':')

		#Paths
		# Tiles mosaic used as background image in Blender
		self.imgPath = folder + self.srcKey + '_' + self.layKey + ".png"
		
		#Init parent MapService class
		super().__init__(self.srcKey, folder)
	
		#Get layer def obj
		self.layer = self.layers[self.layKey]

		#Alias for destination tile matrix
		self.tm = self.tm2

		#Init scene props if not exists
		scn = self.scn
		# scene origin lat long
		if "lat" not in scn and "long" not in scn:
			scn["lat"], scn["long"] = 0.0, 0.0 #explit float for id props
		# zoom level
		if 'z' not in scn:
			scn["z"] = 0
		# EPSG code or proj4 string
		if 'CRS' not in scn:
			scn["CRS"] = '3857' #epsg code web mercator (string id props)
		# scale
		if 'scale' not in scn:
			scn["scale"] = 1 #1:1

		#Read scene props
		self.update()

		#Fake browser header
		self.headers = {
			'Accept' : 'image/png,image/*;q=0.8,*/*;q=0.5' ,
			'Accept-Charset' : 'ISO-8859-1,utf-8;q=0.7,*;q=0.7' ,
			'Accept-Encoding' : 'gzip,deflate' ,
			'Accept-Language' : 'fr,en-us,en;q=0.5' ,
			'Keep-Alive': 115 ,
			'Proxy-Connection' : 'keep-alive' ,
			'User-Agent' : 'Mozilla/5.0 (Windows; U; Windows NT 5.1; fr; rv:1.9.2.13) Gecko/20101003 Firefox/12.0',
			'Referer' : self.referer}

		#Thread attributes
		self.running = False
		self.thread = None
		#Background image attributes
		self.img = None #bpy image
		self.bkg = None #bpy background
		self.img_w, self.img_h = None, None #width, height
		self.img_ox, self.img_oy = None, None #image origin
		#Store list of previous tile number requested
		self.previousCols, self.previousRows = None, None


	#fast access to some properties of destination grid
	@property
	def res(self):
		'''Resolution in meters per pixel for current zoom level'''
		return self.tm.getRes(self.zoom)
	@property
	def CRS(self):
		return self.tm.CRS
	@property
	def tileSize(self):
		return self.tm.tileSize



	def update(self):
		'''Read scene properties and update attributes'''
		#get scene props
		self.zoom = self.scn['z']
		self.scale = self.scn['scale']
		self.lat, self.long = self.scn['lat'], self.scn['long']

		#scene origin coords in projeted system
		self.origin_x, self.origin_y = self.tm.geoToProj(self.long, self.lat)



	def get(self):
		'''Launch run() function in a new thread'''
		self.stop()
		self.running = True
		self.thread = threading.Thread(target=self.run_multi)
		self.thread.start()


	def stop(self):
		'''Stop actual thread'''
		if self.running:
			self.running = False
			self.thread.join()



	def run_multi(self):
		'''Main process, launch multiple thread to retreive tiles and build mosaic'''

		self.update()
		
		self.request()
		
		#List all tiles	
		tiles = [ (c, r) for c in self.cols for r in self.rows]
		
		#test
		#self.getTiles(self.layKey, tiles, self.zoom)
		
		#Create PIL image in memory
		self.mosaic = Image.new("RGBA", (self.img_w , self.img_h), None)

		#reinit cpt progress
		self.nbTiles = len(self.cols) * len(self.rows)
		self.cptTiles = 0	
			
		#Launch threads
		nbThread = 4
		n = len(tiles)
		q = math.ceil(n/nbThread)
		parts = [tiles[i:i+q] for i in range(0, n, q)]
		threads = []
		for part in parts:
			t = threading.Thread(target=self.load, args=(part,))
			threads.append(t)
			t.start()

		# Wait for all threads to complete
		for t in threads:
			t.join()
	
		if self.running:
			#save image
			self.mosaic.save(self.imgPath)			
			#Place background image
			self.place()

		#reinit cpt progress
		self.nbTiles, self.cptTiles = 0, 0



	def progress(self):
		'''Report thread download progress'''
		return self.cptTiles, self.nbTiles  



	def view3dToProj(self, dx, dy):
		'''Convert view3d coords to crs coords'''
		x = self.origin_x + dx
		y = self.origin_y + dy  
		return x, y

	def moveOrigin(self, dx, dy):
		'''Move scene origin and update props'''
		self.origin_x += dx
		self.origin_y += dy
		lon, lat = self.tm.projToGeo(self.origin_x, self.origin_y)
		self.scn["lat"], self.scn["long"] = lat, lon




		
	def request(self):
		'''Compute list of required tiles to cover view3d area'''
		#Get area dimension
		#w, h = self.area.width, self.area.height   
		w, h = self.area3d.width, self.area3d.height
		
		#Get area bbox coords (map origin is bottom lelf)
		xmin = self.origin_x - w/2 * self.res
		ymax = self.origin_y + h/2 * self.res
		xmax = self.origin_x + w/2 * self.res
		ymin = self.origin_y - h/2 * self.res
		bbox = (xmin, ymin, xmax, ymax)

		"""
		#Get first tile indices (tiles matrix origin is top left)
		firstCol, firstRow = self.getTileNumber(xmin, ymax, self.zoom)
		
		#Total number of tiles required
		nbTilesX, nbTilesY = math.ceil(w/self.tileSize), math.ceil(h/self.tileSize)
			
		#Add more tiles because background image will be offseted 
		# and could be to small to cover all area
		nbTilesX += 1
		nbTilesY += 1

		#Build list of required column and row numbers
		self.cols = [firstCol+i for i in range(nbTilesX)]
		if self.originLoc == "NW":
			self.rows = [firstRow+i for i in range(nbTilesY)]
		else:
			self.rows = [firstRow-i for i in range(nbTilesY)]
		"""

		#Get list of required tiles to cover area
		self.cols, self.rows = self.listTiles(bbox, self.zoom)
		
		#Keep first tile (top left) indices
		self.col1, self.row1 = self.cols[0], self.rows[0]

		#Final image size
		self.img_w, self.img_h = len(self.cols) * self.tileSize, len(self.rows) * self.tileSize
		
		#Compute image origin
		#Image origin will not match scene origin, it's why we should offset the image
		img_xmin, img_ymax = self.tm.getTileCoords(self.col1, self.row1, self.zoom) #top left (px center ?)
		self.img_ox = img_xmin + self.img_w/2 * self.res
		self.img_oy = img_ymax - self.img_h/2 * self.res

			
		#Stop thread if the request is same as previous
		if self.previousCols == self.cols and self.previousRows == self.rows:
			self.running = False
		else:
			self.previousCols = self.cols
			self.previousRows = self.rows




	def load(self, tiles):
		'''Get tiles and paste them in mosaic'''
		
		for tile in tiles:
			
			#cancel thread if requested
			if not self.running:
				return			
			
			#unpack col and row indices
			col, row = tile
				
			#Get image bytes data
			data = self.getTile(self.layKey, col, row, self.zoom)
			try:
				#open with PIL
				img = Image.open(io.BytesIO(data))
			except:
				#create an empty tile if we are unable to get a valid stream
				img = Image.new("RGBA", (self.tileSize , self.tileSize), "white")
		
			#Paste tile into mosaic image
			posx = (col - self.col1) * self.tileSize
			posy = abs((row - self.row1)) * self.tileSize		
			self.mosaic.paste(img, (posx, posy))
			
			self.cptTiles += 1


		

	def place(self):
		'''Set map as background image'''
				
		#Get or load bpy image
		try:
			self.img = [img for img in bpy.data.images if img.filepath == self.imgPath][0]
		except:
			self.img = bpy.data.images.load(self.imgPath)

		#Activate view3d background
		self.view3d.show_background_images = True
		
		#Hide all existing background
		for bkg in self.view3d.background_images:
			bkg.show_background_image = False
		
		#Get or load background image
		bkgs = [bkg for bkg in self.view3d.background_images if bkg.image is not None]
		try:
			self.bkg = [bkg for bkg in bkgs if bkg.image.filepath == self.imgPath][0]
		except:
			self.bkg = self.view3d.background_images.new()
			self.bkg.image = self.img

		#Set some props
		self.bkg.show_background_image = True
		self.bkg.view_axis = 'TOP'
		self.bkg.opacity = 1
		
		#Set background size
		sizex = self.img_w * self.res / self.scale
		self.bkg.size = sizex #since blender > 2.74 else = sizex/2
		
		#Set background offset (image origin does not match scene origin)
		dx = (self.origin_x - self.img_ox) / self.scale
		dy = (self.origin_y - self.img_oy) / self.scale
		self.bkg.offset_x = -dx
		ratio = self.img_w / self.img_h
		self.bkg.offset_y = -dy * ratio #https://developer.blender.org/T48034
	
		#Compute view3d z distance
		#in ortho view, view_distance = max(view3d dst x, view3d dist y) / 2
		dst =  max( [self.area3d.width, self.area3d.height] )
		dst = dst * self.res / self.scale
		dst /= 2
		self.reg3d.view_distance = dst
		
		#Update image drawing   
		self.bkg.image.reload()





####################################


def draw_callback(self, context):
	"""Draw map infos on 3dview"""
	
	#Get contexts
	scn = context.scene
	area = context.area
	area3d = [reg for reg in area.regions if reg.type == 'WINDOW'][0]
	view3d = area.spaces.active
	reg3d = view3d.region_3d
	
	#Get area3d dimensions
	w, h = area3d.width, area3d.height
	cx = w/2 #center x

	#Get map props stored in scene
	zoom = scn['z']
	lat, long = scn['lat'], scn['long']
	scale = scn['scale']

	#Set text police and color
	font_id = 0  # ???
	bgl.glColor4f(*scn.fontColor) #rgba
	
	#Draw title
	blf.position(font_id, cx-25, 70, 0) #id, x, y, z
	blf.size(font_id, 15, 72) #id, point size, dpi
	blf.draw(font_id, "Map view")
	
	#Draw other texts
	blf.size(font_id, 12, 72)
	# thread progress
	blf.position(font_id, cx-45, 90, 0)
	if self.nbTotal > 0:
		blf.draw(font_id, '(Downloading... ' + str(self.nb)+'/'+str(self.nbTotal) + ')')
	# zoom and scale values
	blf.position(font_id, cx-50, 50, 0)
	blf.draw(font_id, "Zoom " + str(zoom) + " - Scale 1:" + str(int(scale)))
	# view3d distance
	dst = reg3d.view_distance
	blf.position(font_id, cx-50, 30, 0)
	blf.draw(font_id, '3D View distance ' + str(int(dst)))
	# cursor crs coords
	blf.position(font_id, cx-45, 10, 0)
	blf.draw(font_id, str((int(self.posx), int(self.posy))))



class MAP_VIEW(bpy.types.Operator):

	bl_idname = "view3d.map_view"
	bl_description = 'Toggle 2d map navigation'
	bl_label = "Map viewer"

	fontColor = FloatVectorProperty(name="Font color", subtype='COLOR', min=0, max=1, size=4, default=(0, 0, 0, 1))

	def invoke(self, context, event):
		
		if context.area.type == 'VIEW_3D':
			
			#Add draw callback to view space
			args = (self, context)
			self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback, args, 'WINDOW', 'POST_PIXEL')

			#Add modal handler and init a timer
			context.window_manager.modal_handler_add(self)
			self.timer = context.window_manager.event_timer_add(0.05, context.window)
	
			#Switch to top view ortho (center to origin)
			view3d = context.area.spaces.active
			bpy.ops.view3d.viewnumpad(type='TOP')
			view3d.region_3d.view_perspective = 'ORTHO'
			view3d.cursor_location = (0, 0, 0)
			bpy.ops.view3d.view_center_cursor()
			##view3d.region_3d.view_location = (0, 0, 0)
		
			#Init some properties
			# tag if map is currently drag
			self.inMove = False
			# mouse crs coordinates reported in draw callback
			self.posx, self.posy = 0, 0
			# thread progress infos reported in draw callback
			self.nb, self.nbTotal = 0, 0
	
			#Get map
			self.map = MapImage(context)
			"""
			try:
				self.map = MapImage(context)
			except Exception as e:
				self.report({'ERROR'}, str(e))
				return {'CANCELLED'}
			"""
			self.map.get()
			
			return {'RUNNING_MODAL'}
		
		else:
			
			self.report({'WARNING'}, "View3D not found, cannot run operator")
			return {'CANCELLED'}


	def mouseTo3d(self, context, x, y):
		'''Convert event.mouse_region to world coordinates'''
		coords = (x, y)
		reg = context.region
		reg3d = context.region_data
		vec = region_2d_to_vector_3d(reg, reg3d, coords)
		loc = region_2d_to_location_3d(reg, reg3d, coords, vec)
		return loc


	def modal(self, context, event):
		
		context.area.tag_redraw()
		scn = bpy.context.scene
		
		if event.type == 'TIMER':
			#report thread progression
			self.nb, self.nbTotal = self.map.progress()
			return {'PASS_THROUGH'}
						
		if event.type in ['WHEELUPMOUSE', 'NUMPAD_PLUS']:
			
			if event.value == 'PRESS':
				
				if event.alt:
					# map scale up
					scn['scale'] *= 10
					self.map.scale = scn['scale']
					self.map.place()
				
				elif event.ctrl:
					# view3d zoom up
					context.region_data.view_distance -= 100
				
				else:
					context.region_data.view_distance /= 2 #tile matrix res factor
					# map zoom up
					if scn["z"] < self.map.layer.zmax:
						scn["z"] += 1
						self.map.get()
	
		if event.type in ['WHEELDOWNMOUSE', 'NUMPAD_MINUS']:
			
			if event.value == 'PRESS':
				
				if event.alt:
					#map scale down
					s = scn['scale'] / 10
					if s < 1: s = 1
					scn['scale'] = s
					self.map.scale = s
					self.map.place()
					
				elif event.ctrl:
					#view3d zoom down
					context.region_data.view_distance += 100
					
				else:
					context.region_data.view_distance *= 2
					#map zoom down  
					if scn["z"] > self.map.layer.zmin:
						scn["z"] -= 1
						self.map.get()

		if event.type == 'MOUSEMOVE':
			
			#Report mouse location coords in projeted crs
			loc = self.mouseTo3d(context, event.mouse_region_x, event.mouse_region_y)
			self.posx, self.posy = self.map.view3dToProj(loc.x, loc.y)
			
			#Drag background image (edit its offset values)
			if self.inMove and self.map.bkg is not None:
				loc1 = self.mouseTo3d(context, self.x1, self.y1)
				loc2 = self.mouseTo3d(context, event.mouse_region_x, event.mouse_region_y)
				dx = loc1.x - loc2.x
				dy = loc1.y - loc2.y
				ratio = self.map.img_w / self.map.img_h
				self.map.bkg.offset_x = -dx + self.offset_x
				self.map.bkg.offset_y = (-dy * ratio) + self.offset_y
					
		if event.type in {'LEFTMOUSE', 'MIDDLEMOUSE'}:
			
			if event.value == 'PRESS':
				#Stop thread now, because we don't know when the mouse click will be released
				self.map.stop()
				#Get click mouse position and background image offset (if exist)
				self.x1, self.y1 = event.mouse_region_x, event.mouse_region_y
				if self.map.bkg is not None:
					self.offset_x = self.map.bkg.offset_x
					self.offset_y = self.map.bkg.offset_y
				#Tag that map is currently draging
				self.inMove = True
				
			if event.value == 'RELEASE':
				self.inMove = False
				#Compute final shift
				loc1 = self.mouseTo3d(context, self.x1, self.y1)
				loc2 = self.mouseTo3d(context, event.mouse_region_x, event.mouse_region_y)
				dx = (loc1.x - loc2.x) * self.map.scale
				dy = (loc1.y - loc2.y) * self.map.scale
				#Update map
				self.map.moveOrigin(dx,dy)
				self.map.get()

		if event.type == 'SPACE':
			wm = context.window_manager
			#wm.invoke_popup(self)
			#return wm.invoke_props_dialog(self)

		if event.type in {'ESC'}:
			self.map.stop()
			bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
			return {'CANCELLED'}

		if event.type in {'RET'}:
			self.map.stop()
			bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
			return {'FINISHED'}

		return {'RUNNING_MODAL'}


####################################
# Properties in scene

bpy.types.Scene.fontColor = FloatVectorProperty(name="Font color", subtype='COLOR', min=0, max=1, size=4, default=(0, 0, 0, 1))

bpy.types.Scene.cacheFolder = bpy.props.StringProperty(
      name = "Cache folder",
      default = "",
      description = "Define a folder where to store maptiles db",
      subtype = 'DIR_PATH'
      )


srcItems = []
for srckey, src in sources.items():
	for laykey, lay in src['layers'].items():
		mapkey = srckey + ':' + laykey
		name = src['name'] + " " + lay['name']
		#put each item in a tuple (key, label, tooltip)
		srcItems.append( (mapkey, name, src['description']) )



bpy.types.Scene.mapSource = EnumProperty(
			name = "Map",
			description = "Choose map service source",
			items = srcItems
			)

####################################

class MAP_PANEL(bpy.types.Panel):
	bl_category = "GIS"
	bl_label = "Basemap"
	bl_space_type = "VIEW_3D"
	bl_context = "objectmode"
	bl_region_type = "TOOLS"#"UI"
	

	def draw(self, context):
		layout = self.layout
		scn = context.scene
		layout.prop(scn, "cacheFolder")
		layout.prop(scn, "mapSource")		
		layout.operator("view3d.map_view")
		layout.prop(scn, "fontColor")



