# -*- coding:utf-8 -*-
import os
import bpy
import bmesh
import math
import mathutils
from .shapefile import Reader as shpReader


featureType={
0:'Null',
1:'Point',
3:'PolyLine',
5:'Polygon',
8:'MultiPoint',
11:'PointZ',
13:'PolyLineZ',
15:'PolygonZ',
18:'MultiPointZ',
21:'PointM',
23:'PolyLineM',
25:'PolygonM',
28:'MultiPointM',
31:'MultiPatch'
}


"""
dbf fields type:
	C is ASCII characters
	N is a double precision integer limited to around 18 characters in length
	D is for dates in the YYYYMMDD format, with no spaces or hyphens between the sections
	F is for floating point numbers with the same length limits as N
	L is for logical data which is stored in the shapefile's attribute table as a short integer as a 1 (true) or a 0 (false).
	The values it can receive are 1, 0, y, n, Y, N, T, F or the python builtins True and False
"""

class ellps():
	"""ellipsoid"""
	def __init__(self, a, b):
		self.a =  a#equatorial radius in meters
		self.b =  b#polar radius in meters
		self.f = (self.a-self.b)/self.a#inverse flat
		self.perimeter = (2*math.pi*self.a)#perimeter at equator

ellpsGRS80 = ellps(6378137, 6356752.314245)#ellipsoid GRS80


#TODO use web mercator projection
def dd2meters(val):
	"""
	Convert decimal degrees to meters
	Correct at equator only but it's the way that "plate carré" works, we all know these horizontal distortions...
	"""
	global ellpsGRS80
	return val*(ellpsGRS80.perimeter/360)






#------------------------------------------------------------------------

from bpy_extras.io_utils import ImportHelper #helper class defines filename and invoke() function which calls the file selector
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

class RESET_GEOREF(Operator):
	"""Reset georefs infos stored in scene"""
	bl_idname = "importgis.reset_georef"
	bl_label = "Reset georef"

	def execute(self, context):
		scn = context.scene
		if "Georef X" in scn and "Georef Y" in scn:
			del scn["Georef X"]
			del scn["Georef Y"]
		return{'FINISHED'}


class IMPORT_SHP(Operator, ImportHelper):
	"""Import from ESRI shapefile file format (.shp)"""
	bl_idname = "importgis.shapefile" # important since its how bpy.ops.import.shapefile is constructed (allows calling operator from python console or another script)
	#bl_idname rules: must contain one '.' (dot) charactere, no capital letters, no reserved words (like 'import')
	bl_description = 'Import from ESRI shapefile file format (.shp)'
	bl_label = "Import SHP"
	bl_options = {"UNDO"}

	# ImportHelper class properties
	filename_ext = ".shp"
	filter_glob = StringProperty(
			default = "*.shp",
			options = {'HIDDEN'},
			)

	# List of operator properties, the attributes will be assigned
	# to the class instance from the operator settings before calling.

	# Elevation field
	useFieldElev = BoolProperty(
			name="Elevation from field",
			description="Extract z elevation value from an attribute field",
			default=False
			)
	fieldElevName = StringProperty(name = "Field name")
	#Extrusion field
	useFieldExtrude = BoolProperty(
			name="Extrusion from field",
			description="Extract z extrusion value from an attribute field",
			default=False
			)
	fieldExtrudeName = StringProperty(name = "Field name")
	#Extrusion axis
	extrusionAxis = EnumProperty(
			name="Extrude along",
			description="Select extrusion axis",
			items=[ ('Z', 'z axis', "Extrude along Z axis"),
			('NORMAL', 'Normal', "Extrude along normal")]
			)
	#Decimal degrees to meters
	angCoords = BoolProperty(
			name="Angular coords",
			description="Will convert decimal degrees coordinates to meters",
			default=False
			)
	#Create separate objects
	separateObjects = BoolProperty(
			name="Separate objects",
			description="Import to separate objects instead one large object",
			default=False
			)
	#Name objects from field
	useFieldName = BoolProperty(
			name="Object name from field",
			description="Extract name for created objects from an attribute field",
			default=False
			)
	fieldObjName = StringProperty(name = "Field name")


	def draw(self, context):
		#Function used by blender to draw the panel.
		scn = context.scene
		layout = self.layout
		#
		layout.prop(self, 'useFieldElev')
		if self.useFieldElev:
			layout.prop(self, 'fieldElevName')
		#
		layout.prop(self, 'useFieldExtrude')
		if self.useFieldExtrude:
			layout.prop(self, 'fieldExtrudeName')
			layout.prop(self, 'extrusionAxis')
		#
		layout.prop(self, 'separateObjects')
		if self.separateObjects:
			layout.prop(self, 'useFieldName')
		else:
			self.useFieldName = False
		if self.separateObjects and self.useFieldName:
			layout.prop(self, 'fieldObjName')
		#
		layout.prop(self, 'angCoords')
		#
		if "Georef X" in scn and "Georef Y" in scn:
			isGeoref = True
		else:
			isGeoref = False
		if isGeoref:
			layout.operator("importgis.reset_georef")



	def execute(self, context):

		#Set cursor representation to 'loading' icon
		w = context.window
		w.cursor_set('WAIT') 

		#Toogle object mode and deselect all
		try:
			bpy.ops.object.mode_set(mode='OBJECT')
		except:
			pass

		bpy.ops.object.select_all(action='DESELECT')

		#Path
		filePath = self.filepath
		shpName = os.path.basename(filePath)[:-4]

		#Get shp reader
		print("Read shapefile...")
		try:
			shp = shpReader(filePath)
		except:
			self.report({'ERROR'}, "Unable to read shapefile")
			print("Unable to read shapefile")
			return {'FINISHED'}

		#Check shape type
		shpType = featureType[shp.shapeType]
		print('Feature type : '+shpType)
		if shpType not in ['Point','PolyLine','Polygon','PointZ','PolyLineZ','PolygonZ']:
			self.report({'ERROR'}, "Cannot process multipoint, multipointZ, pointM, polylineM, polygonM and multipatch feature type")
			print("Cannot process multipoint, multipointZ, pointM, polylineM, polygonM and multipatch feature type")
			return {'FINISHED'}

		#Get fields
		fields = [field for field in shp.fields if field[0] != 'DeletionFlag'] #ignore default DeletionFlag field
		fieldsNames = [field[0].lower() for field in fields]#lower() allows case-insensitive
		print("DBF fields : "+str(fieldsNames))

		if self.useFieldName or self.useFieldElev or self.useFieldExtrude:
			self.useDbf = True
		else:
			self.useDbf = False

		if self.useFieldName and self.separateObjects:
			try:
				nameFieldIdx = fieldsNames.index(self.fieldObjName.lower())
			except:
				self.report({'ERROR'}, "Unable to find name field")
				print("Unable to find name field")
				return {'FINISHED'}

		if self.useFieldElev:
			try:
				zFieldIdx = fieldsNames.index(self.fieldElevName.lower())
			except:
				self.report({'ERROR'}, "Unable to find elevation field")
				print("Unable to find elevation field")
				return {'FINISHED'}

			if fields[zFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Elevation field do not contains numeric values")
				print("Elevation field do not contains numeric values")
				return {'FINISHED'}

		if self.useFieldExtrude:
			try:
				extrudeFieldIdx = fieldsNames.index(self.fieldExtrudeName.lower())
			except ValueError:
				self.report({'ERROR'}, "Unable to find extrusion field")
				print("Unable to find extrusion field")
				return {'FINISHED'}

			if fields[extrudeFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Extrusion field do not contains numeric values")
				print("Extrusion field do not contains numeric values")
				return {'FINISHED'}

		#Get bbox
		xmin, ymin, xmax, ymax = shp.bbox
		if self.angCoords:
			xmin, xmax, ymin, ymax = dd2meters(xmin), dd2meters(xmax), dd2meters(ymin), dd2meters(ymax)
		bbox_dx = xmax-xmin
		bbox_dy = ymax-ymin
		center = (xmin+bbox_dx/2, ymin+bbox_dy/2)

		#Get georef dx, dy
		scn = context.scene
		if "Georef X" in scn and "Georef Y" in scn:
			dx, dy = scn["Georef X"], scn["Georef Y"]
		else:
			dx, dy = center[0], center[1]
			#Add custom properties define x & y translation to retrieve georeferenced model
			scn["Georef X"], scn["Georef Y"] = dx, dy	

		#Tag if z will be extracted from shp geoms
		if shpType[-1] == 'Z' and not self.useFieldElev:
			self.useZGeom = True
		else:
			self.useZGeom = False

		#Get reader iterator (using iterator avoids loading all data in memory)
		#warn, shp with zero field will return an empty shapeRecords() iterator
		#to prevent this issue, iter only on shapes if there is no field required
		if self.useDbf:
			#Note: using shapeRecord solve the issue where number of shapes does not match number of table records
			#because it iter only on features with geom and record
			shpIter = shp.iterShapeRecords()
		else:
			shpIter = shp.iterShapes()
		nbFeats = shp.numRecords

		#Init Python lists expected by from_pydata() function
		if not self.separateObjects:
			meshVerts = []
			meshEdges = []
			meshFaces = []

		progress = -1

		#For each feature create a new bmesh
		#using an intermediate bmesh object allows some extra operation like extrusion
		#then extract bmesh data to python list formated as required by from_pydata function
		#using from_pydata is the fatest way to produce a large mesh (appending all geom to the same bmesh is exponentially slow)
		for i, feat in enumerate(shpIter):

			if self.useDbf:
				shape = feat.shape
				record = feat.record
			else:
				shape = feat

			#Progress infos
			pourcent = round(((i+1)*100)/nbFeats)
			if pourcent in list(range(0, 110, 10)) and pourcent != progress:
				progress = pourcent
				print(str(pourcent)+'%')

			#Deal with multipart features
			#If the shape record has multiple parts, the 'parts' attribute will contains the index of 
			#the first point of each part. If there is only one part then a list containing 0 is returned
			if (shpType == 'PointZ' or shpType == 'Point'): #point layer has no attribute 'parts'
				partsIdx = [0]
			else:
				try: #prevent "_shape object has no attribute parts" error
					partsIdx = shape.parts
				except:
					partsIdx = [0]
			nbParts = len(partsIdx)

			#Get list of shape's points
			pts = shape.points
			nbPts = len(pts)

			#Skip null geom
			if nbPts == 0:
				continue #go to next iteration of the loop

			#Get extrusion offset
			if self.useFieldExtrude:
				try:
					offset = float(record[extrudeFieldIdx])

				except:
					offset = 0 #null values will be set to zero

			#Create an empty BMesh
			bm = bmesh.new()

			#Iter over parts
			for j in range(nbParts):

				# EXTRACT 3D GEOM

				geom = [] #will contains a list of 3d points

				#Find first and last part index
				idx1 = partsIdx[j]
				if j+1 == nbParts:
					idx2 = nbPts
				else:
					idx2 = partsIdx[j+1]
			
				#Build 3d geom
				for k, pt in enumerate(pts[idx1:idx2]):
					if self.useFieldElev:
						try:
							z = float(record[zFieldIdx])
						except:
							z = 0 #null values will be set to zero
					elif self.useZGeom:
						z = shape.z[idx1:idx2][k]
					else:
						z = 0
					geom.append((pt[0], pt[1], z))


				#Shift coords and convert degrees to meters if needed
				if self.angCoords:
					geom = [(dd2meters(pt[0])-dx, dd2meters(pt[1])-dy, pt[2]) for pt in geom]
				else:
					geom = [(pt[0]-dx, pt[1]-dy, pt[2]) for pt in geom]

	
				# BUILD BMESH 

				# POINTS
				if (shpType == 'PointZ' or shpType == 'Point'):
					vert = [bm.verts.new(pt) for pt in geom]
					#Extrusion
					if self.useFieldExtrude and offset > 0:
						vect = (0, 0, offset) #along Z
						result = bmesh.ops.extrude_vert_indiv(bm, verts=vert)
						verts = result['verts']
						bmesh.ops.translate(bm, verts=verts, vec=vect)

				# LINES
				if (shpType == 'PolyLine' or shpType == 'PolyLineZ'):
					#Split polyline to lines
					n = len(geom)
					lines = [ (geom[i], geom[i+1]) for i in range(n) if i < n-1 ]
					#Build edges
					edges = []
					for line in lines:
						verts = [bm.verts.new(pt) for pt in line]
						edge = bm.edges.new(verts)
						edges.append(edge)
					#Extrusion
					if self.useFieldExtrude and offset > 0:
						vect = (0, 0, offset) # along Z
						result = bmesh.ops.extrude_edge_only(bm, edges=edges)
						verts = [elem for elem in result['geom'] if isinstance(elem, bmesh.types.BMVert)]
						bmesh.ops.translate(bm, verts=verts, vec=vect)

				# NGONS
				if (shpType == 'Polygon' or shpType == 'PolygonZ'):
					#According to the shapefile spec, polygons points are clockwise and polygon holes are counterclockwise
					#in Blender face is up if points are in anticlockwise order
					geom.reverse() #face up
					geom.pop() #exlude last point because it's the same as first pt
					if len(geom) >= 3: #needs 3 points to get a valid face
						verts = [bm.verts.new(pt) for pt in geom]
						face = bm.faces.new(verts)
						if face.normal < 0: #this is a polygon hole, bmesh cannot handle polygon hole
							pass #TODO
						#Extrusion
						if self.useFieldExtrude and offset > 0:
							#update normal to avoid null vector
							bm.normal_update()
							#build translate vector
							if self.extrusionAxis == 'NORMAL':
								normal = face.normal
								vect = normal*offset
							elif self.extrusionAxis == 'Z':
								vect=(0, 0, offset)
							faces = bmesh.ops.extrude_discrete_faces(bm, faces=[face], use_select_history=False) #{'faces': [BMFace]}
							verts = faces['faces'][0].verts
							bmesh.ops.translate(bm, verts=verts, vec=vect)				


			#Clean up and update the bmesh
			bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
			bm.verts.index_update()
			bm.edges.index_update()
			bm.faces.index_update()

			if self.separateObjects:

				if self.useFieldName:
					try:
						name = record[nameFieldIdx]
					except:
						name = ''
					# null values will return a bytes object containing a blank string of length equal to fields length definition
					if isinstance(name, bytes):
						name = ''
					else:
						name = str(name)
				else:
					name = shpName

				#Calc bmesh bbox
				_xmin = min([pt.co.x for pt in bm.verts])
				_xmax = max([pt.co.x for pt in bm.verts])
				_ymin = min([pt.co.y for pt in bm.verts])
				_ymax = max([pt.co.y for pt in bm.verts])
				_zmin = min([pt.co.z for pt in bm.verts])
				_zmax = max([pt.co.z for pt in bm.verts])

				#Calc bmesh geometry origin and translate coords according to it
				#then object location will be set to initial bmesh origin
				#its a work around to bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
				ox = (_xmin + ((_xmax - _xmin) / 2))
				oy = (_ymin + ((_ymax - _ymin) / 2))
				oz = _zmin
				bmesh.ops.translate(bm, verts=bm.verts, vec=(-ox, -oy, -oz))

				#Create new mesh from bmesh
				mesh = bpy.data.meshes.new(name)
				bm.to_mesh(mesh)

				#Validate new mesh
				if mesh.validate():
					print('Imported mesh had some problem, check the result!')

				#Place obj
				obj = bpy.data.objects.new(name, mesh)
				context.scene.objects.link(obj)
				context.scene.objects.active = obj
				obj.select = True
				obj.location = (ox, oy, oz)

				# bpy operators can be very cumbersome when scene contains lot of objects
				# because it cause implicit scene updates calls
				# so we must avoid using operators when created many objects with the 'separate objects' option)
				##bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
				

			else:
				#Extent lists with bmesh data
				offset = len(meshVerts)
				meshVerts.extend(v.co[:] for v in bm.verts)
				meshEdges.extend([[v.index + offset for v in e.verts] for e in bm.edges])
				meshFaces.extend([[v.index + offset for v in f.verts] for f in bm.faces])

			bm.free()
		

		#using from_pydata to create the final mesh
		if not self.separateObjects:
			
			mesh = bpy.data.meshes.new(shpName)
			mesh.from_pydata(meshVerts, meshEdges, meshFaces)

			#Validate new mesh
			if mesh.validate():
				print('Imported mesh had some problem, check the result!')

			obj = bpy.data.objects.new(shpName, mesh)
			context.scene.objects.link(obj)
			context.scene.objects.active = obj
			obj.select = True

			bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')



		#Adjust grid size
		# get object(s) bbox in 3dview from previously computed shapefile bbox
		xmin -= dx
		xmax -= dx
		ymin -= dy
		ymax -= dy
		# grid size and clip distance
		dstMax = round(max(abs(xmax), abs(xmin), abs(ymax), abs(ymin)))*2
		nbDigit = len(str(dstMax))
		scale = 10**(nbDigit-2)#1 digits --> 0.1m, 2 --> 1m, 3 --> 10m, 4 --> 100m, , 5 --> 1000m
		nbLines = round(dstMax/scale)
		targetDst = nbLines*scale
		# set each 3d view
		areas = context.screen.areas
		for area in areas:
			if area.type == 'VIEW_3D':
				space = area.spaces.active
				#Adjust floor grid and clip distance if the new obj is largest than actual settings
				if space.grid_lines*space.grid_scale < targetDst:
					space.grid_lines = nbLines
					space.grid_scale = scale
					space.clip_end = targetDst*10 #10x more than necessary
				#Zoom to selected
				overrideContext = {'area': area, 'region':area.regions[-1]}
				bpy.ops.view3d.view_selected(overrideContext)


		return {'FINISHED'}

