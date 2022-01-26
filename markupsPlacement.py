import os
import sys
import shutil
import pandas as pd
import numpy as np
import csv
import json
import glob
import vtk, qt, slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin

if getattr(sys, 'frozen', False):
	cwd = os.path.dirname(sys.argv[0])
elif __file__:
	cwd = os.path.dirname(os.path.realpath(__file__))

sys.path.insert(1, os.path.dirname(cwd))

from helpers.helpers import vtkModelBuilderClass,getFrameCenter, getReverseTransform,\
addCustomLayouts, hex2rgb, sorted_nicely, sortSceneData
from helpers.variables import coordSys, slicerLayout, surgical_info_dict

#
# dataImport
#

class markupsPlacement(ScriptedLoadableModule):
	"""Uses ScriptedLoadableModule base class, available at:
	https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self, parent):
		ScriptedLoadableModule.__init__(self, parent)
		self.parent.title = "Markups Placement"
		self.parent.categories = ["IO"]
		self.parent.dependencies = []
		self.parent.contributors = ["Greydon Gilmore (Western University)"]
		self.parent.helpText = """
This module allows placement of control points.
"""
		self.parent.acknowledgementText = ""


#
# markupsPlacementWidget
#

class markupsPlacementWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
	"""Uses ScriptedLoadableModuleWidget base class, available at:
	https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self, parent=None):
		"""
		Called when the user opens the module the first time and the widget is initialized.
		"""
		ScriptedLoadableModuleWidget.__init__(self, parent)
		VTKObservationMixin.__init__(self)  # needed for parameter node observation
		self.logic = None
		self._parameterNode = None
		self.refVolPath = None
		self.floatVolPath = None
		self._updatingGUIFromParameterNode = False
		self.transform_directory = []
		self.observerTags = [] 

	def setup(self):
		"""
		Called when the user opens the module the first time and the widget is initialized.
		"""
		ScriptedLoadableModuleWidget.setup(self)
		
		self._loadUI()
		
		# Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
		# "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
		# "setMRMLScene(vtkMRMLScene*)" slot.
		
		# Create logic class. Logic implements all computations that should be possible to run
		# in batch mode, without a graphical user interface.
		self.logic = markupsPlacementLogic()
		
		# Connections
		self._setupConnections()
		
	def _loadUI(self):
		# Load widget from .ui file (created by Qt Designer)
		self.uiWidget = slicer.util.loadUI(self.resourcePath('UI/markupsPlacement.ui'))
		self.layout.addWidget(self.uiWidget)
		self.ui = slicer.util.childWidgetVariables(self.uiWidget)
		self.uiWidget.setMRMLScene(slicer.mrmlScene)

		self.ui.allLock.setIcon(qt.QIcon(self.resourcePath('Icons/SlicerLockUnlock.png')))
		self.ui.allVis.setIcon(qt.QIcon(self.resourcePath('Icons/SlicerVisibleInvisible.png')))

		self.ui.tableWidget.setEditTriggers(qt.QAbstractItemView.NoEditTriggers)

		self.ui.tableWidget.setColumnWidth(0, 25)
		self.ui.tableWidget.setColumnWidth(1, 25)
		self.ui.tableWidget.setColumnWidth(2, 80)
		self.ui.tableWidget.setColumnWidth(3, 100)
		self.ui.tableWidget.setColumnWidth(4, 80)
		self.ui.tableWidget.setColumnWidth(5, 80)
		self.ui.tableWidget.setColumnWidth(6, 80)

		self.ui.MRMLNodeComboBox.setMRMLScene(slicer.mrmlScene)

	def _setupConnections(self):
		# These connections ensure that we update parameter node when scene is closed
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)
		
		# These connections ensure that whenever user changes some settings on the GUI, that is saved in the MRML scene
		# (in the selected parameter node).
		
		self.ui.MRMLNodeComboBox.connect('currentNodeChanged(bool)', self.onNodeCBox)
		self.ui.tableWidget.cellClicked.connect(self.onCellClicked)
		self.ui.tableWidget.cellDoubleClicked.connect(self.onCellDoubleClicked)

		self.ui.allLock.clicked.connect(lambda: self.onAllButton(self.ui.allLock))
		self.ui.allVis.clicked.connect(lambda: self.onAllButton(self.ui.allVis))

		# Make sure parameter node is initialized (needed for module reload)
		self.initializeParameterNode()
	
	def onAllButton(self, item):
		if self.ui.MRMLNodeComboBox.currentNode() is not None:
			currentMarkupNode = self.ui.MRMLNodeComboBox.currentNode()
			if item.name =='allLock':
				for ipoint in range(currentMarkupNode.GetNumberOfControlPoints()):
					if currentMarkupNode.GetNthControlPointLocked(ipoint):
						currentMarkupNode.SetNthControlPointLocked(ipoint, 0)
						lock_label = qt.QLabel()
						lock_label.setAlignment(qt.Qt.AlignCenter)
						lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationUnlock.png')))
						lock_label.setObjectName(f"unlocked_{ipoint}")
						self.ui.tableWidget.setCellWidget(ipoint, 0, lock_label)
					else:
						currentMarkupNode.SetNthControlPointLocked(ipoint, 1)
						lock_label = qt.QLabel()
						lock_label.setAlignment(qt.Qt.AlignCenter)
						lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationLock.png')))
						lock_label.setObjectName(f"locked_{ipoint}")
						self.ui.tableWidget.setCellWidget(ipoint, 0, lock_label)
			elif item.name =='allVis':
				for ipoint in range(currentMarkupNode.GetNumberOfControlPoints()):
					if currentMarkupNode.GetNthControlPointVisibility(ipoint):
						currentMarkupNode.SetNthControlPointVisibility(ipoint, 0)
						vis_label = qt.QLabel()
						vis_label.setAlignment(qt.Qt.AlignCenter)
						vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationInvisible.png')))
						vis_label.setObjectName(f"invisible_{ipoint}")
						self.ui.tableWidget.setCellWidget(ipoint, 1, vis_label)
					else:
						currentMarkupNode.SetNthControlPointVisibility(ipoint, 1)
						vis_label = qt.QLabel()
						vis_label.setAlignment(qt.Qt.AlignCenter)
						vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationVisibility.png')))
						vis_label.setObjectName(f"visible_{ipoint}")
						self.ui.tableWidget.setCellWidget(ipoint, 1, vis_label)

	def onCellDoubleClicked(self):
		row=self.ui.tableWidget.currentRow()
		column=self.ui.tableWidget.currentColumn()
		item = self.ui.tableWidget.cellWidget(row, column)
		currentMarkupNode = self.ui.MRMLNodeComboBox.currentNode()
		if item.name.startswith('name'):
			self.ui.tableWidget.setEditTriggers(qt.Qt.ItemIsEditable)

	def onNodeCBox(self):
		if self.ui.MRMLNodeComboBox.currentNode() is not None:
			currentMarkupNode = self.ui.MRMLNodeComboBox.currentNode()

			self.removeLandmarkObservers()

			tag = currentMarkupNode.AddObserver(currentMarkupNode.PointModifiedEvent, lambda caller,event: self.onPointMoved(caller))
			self.observerTags.append( (currentMarkupNode,tag) )
			tag = currentMarkupNode.AddObserver(currentMarkupNode.PointEndInteractionEvent, lambda caller,event: self.onPointEndMoving(caller))
			self.observerTags.append( (currentMarkupNode,tag) )
			

			self.ui.tableWidget.setRowCount(currentMarkupNode.GetNumberOfControlPoints())
			for ipoint in range(currentMarkupNode.GetNumberOfControlPoints()):
				if currentMarkupNode.GetNthControlPointLocked(ipoint):
					lock_label = qt.QLabel()
					lock_label.setAlignment(qt.Qt.AlignCenter)
					lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationLock.png')))
					lock_label.setObjectName(f"locked_{ipoint}")
					self.ui.tableWidget.setCellWidget(ipoint, 0, lock_label)
				else:
					lock_label = qt.QLabel()
					lock_label.setAlignment(qt.Qt.AlignCenter)
					lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationUnlock.png')))
					lock_label.setObjectName(f"unlocked_{ipoint}")
					self.ui.tableWidget.setCellWidget(ipoint, 0, lock_label)
				
				if currentMarkupNode.GetNthControlPointVisibility(ipoint):
					vis_label = qt.QLabel()
					vis_label.setAlignment(qt.Qt.AlignCenter)
					vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationVisibility.png')))
					vis_label.setObjectName(f"visible_{ipoint}")
					self.ui.tableWidget.setCellWidget(ipoint, 1, vis_label)
				else:
					vis_label = qt.QLabel()
					vis_label.setAlignment(qt.Qt.AlignCenter)
					vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationInvisible.png')))
					vis_label.setObjectName(f"invisible_{ipoint}")
					self.ui.tableWidget.setCellWidget(ipoint, 1, vis_label)

				name_label = qt.QLabel(currentMarkupNode.GetNthControlPointLabel(ipoint))
				name_label.setAlignment(qt.Qt.AlignCenter)
				name_label.setObjectName(f"name_{currentMarkupNode.GetNthControlPointLabel(ipoint)}_{ipoint}")
				self.ui.tableWidget.setCellWidget(ipoint, 2, name_label)
				
				desc_label = qt.QLabel(currentMarkupNode.GetNthControlPointDescription(ipoint))
				desc_label.setAlignment(qt.Qt.AlignCenter)
				desc_label.setObjectName(f"desc_{currentMarkupNode.GetNthControlPointDescription(ipoint)}_{ipoint}")
				self.ui.tableWidget.setCellWidget(ipoint, 3, desc_label)

				point_coord = np.zeros(3)
				currentMarkupNode.GetNthControlPointPositionWorld(ipoint, point_coord)

				x_label = qt.QLabel(f"{point_coord[0]:.3f}")
				x_label.setAlignment(qt.Qt.AlignCenter)
				x_label.setObjectName(f"x_{ipoint}")
				self.ui.tableWidget.setCellWidget(ipoint, 4, x_label)

				y_label = qt.QLabel(f"{point_coord[1]:.3f}")
				y_label.setAlignment(qt.Qt.AlignCenter)
				y_label.setObjectName(f"y_{ipoint}")
				self.ui.tableWidget.setCellWidget(ipoint, 5, y_label)

				z_label = qt.QLabel(f"{point_coord[2]:.3f}")
				z_label.setAlignment(qt.Qt.AlignCenter)
				z_label.setObjectName(f"z_{ipoint}")
				self.ui.tableWidget.setCellWidget(ipoint, 6, z_label)

	def removeLandmarkObservers(self):
		"""Remove any existing observers"""
		for obj,tag in self.observerTags:
			obj.RemoveObserver(tag)
		self.observerTags = []

	def onPointMoved(self, pointList):
		"""Callback when pointList's point has been changed.
		Check the Markups.State attribute to see if it is being
		actively moved and if so, skip the picked method."""
		self.movingView = pointList.GetAttribute('Markups.MovingInSliceView')
		movingIndexAttribute = pointList.GetAttribute('Markups.MovingMarkupIndex')
		if self.movingView and movingIndexAttribute:
			movingIndex = int(movingIndexAttribute)
			if movingIndex < pointList.GetNumberOfDefinedControlPoints():
				landmarkName = pointList.GetNthControlPointLabel(movingIndex)

	def onPointEndMoving(self,pointList):
		"""Callback when pointList's point is done moving."""
		movingIndexAttribute = pointList.GetAttribute('Markups.MovingMarkupIndex')
		if movingIndexAttribute:
			movingIndex = int(movingIndexAttribute)
			landmarkName = pointList.GetNthControlPointLabel(movingIndex)
			point_coord = np.zeros(3)
			pointList.GetNthControlPointPositionWorld(movingIndex, point_coord)

			x_label = qt.QLabel(f"{point_coord[0]:.3f}")
			x_label.setAlignment(qt.Qt.AlignCenter)
			x_label.setObjectName(f"x_{movingIndex}")
			self.ui.tableWidget.setCellWidget(movingIndex, 4, x_label)

			y_label = qt.QLabel(f"{point_coord[1]:.3f}")
			y_label.setAlignment(qt.Qt.AlignCenter)
			y_label.setObjectName(f"y_{movingIndex}")
			self.ui.tableWidget.setCellWidget(movingIndex, 5, y_label)

			z_label = qt.QLabel(f"{point_coord[2]:.3f}")
			z_label.setAlignment(qt.Qt.AlignCenter)
			z_label.setObjectName(f"z_{movingIndex}")
			self.ui.tableWidget.setCellWidget(movingIndex, 6, z_label)

			print(f"{landmarkName}")
	
	def onCellClicked(self):
		row=self.ui.tableWidget.currentRow()
		column=self.ui.tableWidget.currentColumn()
		item = self.ui.tableWidget.cellWidget(row, column)
		currentMarkupNode = self.ui.MRMLNodeComboBox.currentNode()

		if isinstance(item, qt.QLabel):
			if item.name.startswith('locked'):
				currentMarkupNode.SetNthControlPointLocked(int(item.name.split('_')[-1]), 0)
				
				lock_label = qt.QLabel()
				lock_label.setAlignment(qt.Qt.AlignCenter)
				lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationUnlock.png')))
				lock_label.setObjectName(f"unlocked_{item.name.split('_')[-1]}")
				self.ui.tableWidget.setCellWidget(row, column, lock_label)
			elif item.name.startswith('unlocked'):
				currentMarkupNode.SetNthControlPointLocked(int(item.name.split('_')[-1]), 1)
				
				lock_label = qt.QLabel()
				lock_label.setAlignment(qt.Qt.AlignCenter)
				lock_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationLock.png')))
				lock_label.setObjectName(f"locked_{item.name.split('_')[-1]}")
				self.ui.tableWidget.setCellWidget(row, column, lock_label)
			elif item.name.startswith('visible'):
				currentMarkupNode.SetNthControlPointVisibility(int(item.name.split('_')[-1]), 0)
				
				vis_label = qt.QLabel()
				vis_label.setAlignment(qt.Qt.AlignCenter)
				vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationInvisible.png')))
				vis_label.setObjectName(f"invisible_{item.name.split('_')[-1]}")
				self.ui.tableWidget.setCellWidget(row, column, vis_label)
			elif item.name.startswith('invisible'):
				currentMarkupNode.SetNthControlPointVisibility(int(item.name.split('_')[-1]), 1)
				
				vis_label = qt.QLabel()
				vis_label.setAlignment(qt.Qt.AlignCenter)
				vis_label.setPixmap(qt.QPixmap(self.resourcePath('Icons/AnnotationVisibility.png')))
				vis_label.setObjectName(f"visible_{item.name.split('_')[-1]}")
				self.ui.tableWidget.setCellWidget(row, column, vis_label)
			
		if self.ui.jumpSlice.isChecked():
			point_coord = np.zeros(3)
			currentMarkupNode.GetNthControlPointPositionWorld(row, point_coord)
			slicer.util.getNode('vtkMRMLSliceNodeRed').JumpSliceByCentering(point_coord[0],point_coord[1],point_coord[2])
			slicer.util.getNode('vtkMRMLSliceNodeGreen').JumpSliceByCentering(point_coord[0],point_coord[1],point_coord[2])
			slicer.util.getNode('vtkMRMLSliceNodeYellow').JumpSliceByCentering(point_coord[0],point_coord[1],point_coord[2])

	def cleanup(self):
		"""
		Called when the application closes and the module widget is destroyed.
		"""
		self.removeObservers()
		
	def enter(self):
		"""
		Called each time the user opens this module.
		"""
		# Make sure parameter node exists and observed
		self.initializeParameterNode()
		
	def exit(self):
		"""
		Called each time the user opens a different module.
		"""
		# Do not react to parameter node changes (GUI wlil be updated when the user enters into the module)
		self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

	def onSceneStartClose(self, caller, event):
		"""
		Called just before the scene is closed.
		"""
		# Parameter node will be reset, do not use it anymore
		self.setParameterNode(None)

	def onSceneEndClose(self, caller, event):
		"""
		Called just after the scene is closed.
		"""
		# If this module is shown while the scene is closed then recreate a new parameter node immediately
		if self.parent.isEntered:
			self.initializeParameterNode()
			#self.ui.tableWidget.clear()
			self.ui.tableWidget.setRowCount(0)

	def initializeParameterNode(self):
		"""
		Ensure parameter node exists and observed.
		"""
		# Parameter node stores all user choices in parameter values, node selections, etc.
		# so that when the scene is saved and reloaded, these settings are restored.

		if self._parameterNode is not None:
			self.setParameterNode(self.logic.getParameterNode())

	def setParameterNode(self, inputParameterNode):
		"""
		Set and observe parameter node.
		Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
		"""

		if inputParameterNode:
			self.logic.setDefaultParameters(inputParameterNode)

		# Unobserve previously selected parameter node and add an observer to the newly selected.
		# Changes of parameter node are observed so that whenever parameters are changed by a script or any other module
		# those are reflected immediately in the GUI.
		if self._parameterNode is not None:
			self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)
		self._parameterNode = inputParameterNode
		if self._parameterNode is not None:
			self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

		# Initial GUI update
		self.updateGUIFromParameterNode()

	def updateGUIFromParameterNode(self, caller=None, event=None):
		"""
		This method is called whenever parameter node is changed.
		The module GUI is updated to show the current state of the parameter node.
		"""

		if self._parameterNode is None or self._updatingGUIFromParameterNode:
			return

		# Make sure GUI changes do not call updateParameterNodeFromGUI (it could cause infinite loop)
		self._updatingGUIFromParameterNode = True

		# All the GUI updates are done
		self._updatingGUIFromParameterNode = False

	def updateParameterNodeFromGUI(self, caller=None, event=None):
		"""
		This method is called when the user makes any change in the GUI.
		The changes are saved into the parameter node (so that they are restored when the scene is saved and loaded).
		"""

		if self._parameterNode is None or self._updatingGUIFromParameterNode:
			return

		#wasModified = self._parameterNode.StartModify()  # Modify all properties in a single batch
		#self._parameterNode.EndModify(wasModified)

#
# markupsPlacementLogic
#

class markupsPlacementLogic(ScriptedLoadableModuleLogic):
	"""This class should implement all the actual
	computation done by your module.  The interface
	should be such that other python code can import
	this class and make use of the functionality without
	requiring an instance of the Widget.
	Uses ScriptedLoadableModuleLogic base class, available at:
	https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self):
		ScriptedLoadableModuleLogic.__init__(self)

		self.scriptPath = os.path.dirname(os.path.abspath(__file__))
		self._parameterNode = None

	def getParameterNode(self, replace=False):
		"""Get the dataImport parameter node.

		"""
		node = self._findParameterNodeInScene()
		if not node:
			node = self._createParameterNode()
		if replace:
			slicer.mrmlScene.RemoveNode(node)
			node = self._createParameterNode()
		return node

	def _findParameterNodeInScene(self):
		node = None
		for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLScriptedModuleNode")):
			if slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLScriptedModuleNode").GetModuleName() == "markupsPlacement":
				node = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLScriptedModuleNode")
				break
		return node

	def _createParameterNode(self):
		""" Create the dataImport parameter node.

		This is used internally by getParameterNode - shouldn't really
		be called for any other reason.

		"""
		node = slicer.vtkMRMLScriptedModuleNode()
		node.SetSingletonTag("markupsPlacement")
		node.SetModuleName("markupsPlacement")
		self.setDefaultParameters(node)
		slicer.mrmlScene.AddNode(node)
		# Since we are a singleton, the scene won't add our node into the scene,
		# but will instead insert a copy, so we find that and return it
		node = self._findParameterNodeInScene()
		return node

	def setDefaultParameters(self, parameterNode):
		"""
		Initialize parameter node with default settings.
		"""
		if getattr(sys, 'frozen', False):
			markupsPlacementPath = os.path.dirname(sys.argv[0])
		elif __file__:
			markupsPlacementPath = os.path.dirname(os.path.realpath(__file__))

		if not parameterNode.GetParameter("markupsPlacementPath"):
			parameterNode.SetParameter("markupsPlacementPath", markupsPlacementPath)


