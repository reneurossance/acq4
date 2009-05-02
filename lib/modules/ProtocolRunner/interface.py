# -*- coding: utf-8 -*-
from lib.modules.Module import *
from ProtocolRunnerTemplate import *
from PyQt4 import QtGui, QtCore
from lib.util.DirTreeModel import *
from lib.util.configfile import *
from lib.util.advancedTypes import OrderedDict
from lib.util.SequenceRunner import *
from lib.util.WidgetGroup import *
import time

class ProtocolRunner(Module, QtCore.QObject):
    def __init__(self, manager, name, config):
        Module.__init__(self, manager, name, config)
        QtCore.QObject.__init__(self)
        
        self.devListItems = {}
        #self.seqListItems = OrderedDict()  ## Looks like {(device, param): listItem, ...}
        self.docks = {}
        self.deleteState = 0
        self.ui = Ui_MainWindow()
        self.win = QtGui.QMainWindow()
        self.ui.setupUi(self.win)
        self.ui.sequenceParamList.header().setResizeMode(QtGui.QHeaderView.Stretch)
        self.protoStateGroup = WidgetGroup([
            (self.ui.protoContinuousCheck, 'continuous'),
            (self.ui.protoDurationSpin, 'duration'),
            (self.ui.protoLeadTimeSpin, 'leadTime'),
            (self.ui.protoLoopCheck, 'loop'),
            (self.ui.protoCycleTimeSpin, 'loopCycleTime'),
            (self.ui.seqCycleTimeSpin, 'cycleTime'),
        ])
        
        self.protocolList = DirTreeModel(self.config['globalDir'])
        self.ui.protocolList.setModel(self.protocolList)
        
        self.currentProtocol = None   ## pointer to current protocol object
        
        #self.updateDeviceList()
        
        self.newProtocol()
        
        self.taskThread = TaskThread(self)
        
        QtCore.QObject.connect(self.ui.newProtocolBtn, QtCore.SIGNAL('clicked()'), self.newProtocol)
        QtCore.QObject.connect(self.ui.saveProtocolBtn, QtCore.SIGNAL('clicked()'), self.saveProtocol)
        QtCore.QObject.connect(self.ui.loadProtocolBtn, QtCore.SIGNAL('clicked()'), self.loadProtocol)
        QtCore.QObject.connect(self.ui.saveAsProtocolBtn, QtCore.SIGNAL('clicked()'), self.saveProtocolAs)
        QtCore.QObject.connect(self.ui.deleteProtocolBtn, QtCore.SIGNAL('clicked()'), self.deleteProtocol)
        QtCore.QObject.connect(self.ui.testSingleBtn, QtCore.SIGNAL('clicked()'), self.testSingle)
        QtCore.QObject.connect(self.ui.runProtocolBtn, QtCore.SIGNAL('clicked()'), self.runSingle)
        QtCore.QObject.connect(self.ui.testSequenceBtn, QtCore.SIGNAL('clicked()'), self.testSequence)
        QtCore.QObject.connect(self.ui.runSequenceBtn, QtCore.SIGNAL('clicked()'), self.runSequence)
        QtCore.QObject.connect(self.ui.deviceList, QtCore.SIGNAL('itemClicked(QListWidgetItem*)'), self.deviceItemClicked)
        QtCore.QObject.connect(self.ui.protoDurationSpin, QtCore.SIGNAL('editingFinished()'), self.protParamsChanged)
        QtCore.QObject.connect(self.ui.protocolList, QtCore.SIGNAL('doubleClicked(const QModelIndex &)'), self.loadProtocol)
        QtCore.QObject.connect(self.ui.protocolList, QtCore.SIGNAL('clicked(const QModelIndex &)'), self.protoListClicked)
        QtCore.QObject.connect(self.protocolList, QtCore.SIGNAL('fileRenamed(PyQt_PyObject, PyQt_PyObject)'), self.fileRenamed)
        QtCore.QObject.connect(self.taskThread, QtCore.SIGNAL('finished()'), self.taskThreadStopped)
        QtCore.QObject.connect(self.taskThread, QtCore.SIGNAL('newFrame(PyQt_PyObject)'), self.handleFrame)
        #QtCore.QObject.connect(self.ui.deviceList, QtCore.SIGNAL('itemChanged(QListWidgetItem*)'), self.deviceItemChanged)
        
        self.win.show()
        
    def getDevice(self, dev):
        if dev not in self.docks:
            ## Create the device if needed
            try:
                item = self.ui.deviceList.findItems(dev, QtCore.Qt.MatchExactly)[0]
            except:
                raise Exception('Requested device %s does not exist!' % dev)
            item.setCheckState(QtCore.Qt.Checked)
            self.deviceItemClicked(item)
            #self.docks[dev].show()
        return self.docks[dev].widget()
        
    def getParam(self, param):
        return self.currentProtocol.conf[param]
        
    def updateDeviceList(self, protocol=None):
        """Read the list of devices from the device manager"""
        devList = self.manager.listDevices()
        
        if protocol is not None:
            protList = protocol.devices.keys()
        elif self.currentProtocol is not None:
            protList = self.currentProtocol.devices.keys()
        else:
            protList = []
            
        ## Remove all devices that do not exist and are not referenced by the protocol
        rem = []
        for d in self.devListItems:
            if d not in devList and d not in protList:
                #print "    ", d
                self.ui.deviceList.takeItem(self.ui.deviceList.row(self.devListItems[d]))
                rem.append(d)
        for d in rem:
            del self.devListItems[d]
                
        ## Add all devices that exist in the current system
        for d in devList:
            if d not in self.devListItems:
                self.devListItems[d] = QtGui.QListWidgetItem(d, self.ui.deviceList)
                self.devListItems[d].setData(32, QtCore.QVariant(d))
            self.devListItems[d].setForeground(QtGui.QBrush(QtGui.QColor(0,0,0)))
            
            
        ## Add all devices that are referenced by the protocol but do not exist
        
        for d in protList:
            if d not in self.devListItems:
                self.devListItems[d] = QtGui.QListWidgetItem(d, self.ui.deviceList)
                self.devListItems[d].setForeground(QtGui.QBrush(QtGui.QColor(150,0,0)))
            
        ## Make sure flags and checkState are correct for all items
        for d in self.devListItems:
            self.devListItems[d].setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable )
            if d in protList:
                self.devListItems[d].setCheckState(QtCore.Qt.Checked)
            else:
                self.devListItems[d].setCheckState(QtCore.Qt.Unchecked)
            
    #def deviceItemChanged(self, item):
        #newName = str(item.text())
        #oldName = str(item.data(32).toString())
        #if newName == oldName:
            #return
        
        ### If the new name does exist:
          ### If the types are compatible, rename and update the new dock
          ### If the types are incompatible, reject the rename
          
          
        #if newName in self.devListItems:
            ### Destroy old dock if needed
            #if newName in self.currentProtocol.enabledDevices():
                #self.devListItems[newName].setCheckState(QtCore.Qt.Unchecked)
                #self.updateDocks()
            ### remove from list
            #self.ui.deviceList.takeItem(self.devListItems[newName])
          
        ### if the new name doesn't exist, just accept the rename and update the device list
            
        #item.setData(32, QtCore.QVariant(newName))
        #self.devListItems[newName] = item
        #del self.devListItems[oldName]
        #self.currentProtocol.renameDevice(oldName, newName)
        #self.updateDeviceList()
        
        ### If the new name is an existing device, load and configure its dock
        #if newName in self.manager.listDevices():
            #self.updateDocks()
        
        ### Configure docks
        #if newName in self.docks:
            #self.docks[newName].widget().restoreState(self.currentProtocol.conf['devices'][newName])
            
            ### Configure dock positions
            #if 'winState' in self.currentProtocol.conf:
                #self.win.restoreState(QtCore.QByteArray.fromPercentEncoding(self.currentProtocol.conf['winState']))
            
    def protoListClicked(self, ind):
        ## Check to see if the selection has changed
        sel = list(self.ui.protocolList.selectedIndexes())
        if len(sel) == 1:
            self.ui.deleteProtocolBtn.setEnabled(True)
        else:
            self.ui.deleteProtocolBtn.setEnabled(False)
        self.resetDeleteState()
            
    def fileRenamed(self, fn1, fn2):
        ## A file was renamed, we might need to act on this change..
        if fn1 == self.currentProtocol.fileName:
            self.currentProtocol.fileName = fn2
            pn = fn2.replace(self.protocolList.baseDir, '')
            self.ui.currentProtocolLabel.setText(pn)
            return
        if os.path.isdir(fn2) and fn1 in self.currentProtocol.fileName:
            self.currentProtocol.fileName = self.currentProtocol.fileName.replace(fn1, fn2)
            pn = self.currentProtocol.fileName.replace(self.protocolList.baseDir, '')
            self.ui.currentProtocolLabel.setText(pn)
            return
            
    def updateSeqParams(self, dev):
        if dev not in self.currentProtocol.enabledDevices():
            return
        params = self.docks[dev].widget().listSequence()
        # Catalog the parameters that already exist for this device:
        items = {}
        for i in self.ui.sequenceParamList.findItems(dev, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0):
            items[str(i.text(1))] = i
        ## Add new sequence parameters, update old ones
        for p in params:
            if p not in items:
                item = QtGui.QTreeWidgetItem([dev, p, str(params[p])])
                item.setFlags(
                    QtCore.Qt.ItemIsSelectable | 
                    QtCore.Qt.ItemIsDragEnabled |
                    QtCore.Qt.ItemIsDropEnabled |
                    QtCore.Qt.ItemIsUserCheckable |
                    QtCore.Qt.ItemIsEnabled)
                items[p] = item
                self.ui.sequenceParamList.addTopLevelItem(item)
            items[p].setData(2, QtCore.Qt.DisplayRole, QtCore.QVariant(str(params[p])))
            
        ## remove non-existent sequence parameters
        for key in items:
            if key not in params:
                item = items[key]
                childs = item.takeChildren()
                p = item.parent()
                if p is None:
                    ind = self.ui.sequenceParamList.indexOfTopLevelItem(item)
                    self.ui.sequenceParamList.takeTopLevelItem(ind)
                    for c in childs:
                        self.ui.sequenceParamList.addTopLevelItem(c)
                else:
                    p.removeChild(item)
                    for c in childs:
                        p.addChild(c)
                        
        
    def hideDock(self, dev):
        #print "hiding", dev
        self.docks[dev].hide()
        items = self.ui.sequenceParamList.findItems(dev, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
        for i in items:
            i.setHidden(True)
        
    def showDock(self, dev):
        #print "showing", dev
        self.docks[dev].show()
        items = self.ui.sequenceParamList.findItems(dev, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
        for i in items:
            i.setHidden(False)
        
    def updateDocks(self, protocol = None):
        if protocol is None:
            protocol = self.currentProtocol
        #print "update docks", protocol.name
        #print "  devices:", protocol.enabledDevices()
        
        ## (un)hide docks as needed
        for d in self.docks:
            #print "  check", d
            if self.docks[d] is None:
                continue
            if d not in protocol.enabledDevices():
                #print "  hide", d
                self.hideDock(d)
            else:
                #print "  show", d
                self.showDock(d)
            
        ## Create docks that don't exist
        for d in protocol.enabledDevices():
            if d not in self.docks:
                if d not in self.manager.listDevices():
                    continue
                self.docks[d] = None  ## Instantiate to prevent endless loops!
                #print "  Create", d
                dev = self.manager.getDevice(d)
                dw = dev.protocolInterface(self)
                dock = QtGui.QDockWidget(d)
                dock.setFeatures(dock.AllDockWidgetFeatures)
                dock.setObjectName(d)
                dock.setWidget(dw)
                dock.setAutoFillBackground(True)
                
                self.docks[d] = dock
                self.win.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
                QtCore.QObject.connect(dock.widget(), QtCore.SIGNAL('sequenceChanged'), self.updateSeqParams)
                self.updateSeqParams(d)
        
    def deviceItemClicked(self, item):
        if item.checkState() == QtCore.Qt.Unchecked:
            self.currentProtocol.removeDevice(str(item.text()))
        else:
            self.currentProtocol.addDevice(str(item.text()))
        self.updateDocks()
        
    def clearDocks(self):
        for d in self.docks:
            self.win.removeDockWidget(self.docks[d])
        self.docks = {}
        self.ui.sequenceParamList.clear()
                
        
    def closeProtocol(self):
        ## Remove all docks
        self.clearDocks()
        
        ## Clear sequence list
        self.ui.sequenceList.clearItems()
        
    def protParamsChanged(self):
        self.currentProtocol.conf = self.protoStateGroup.state()
        #self.currentProtocol.conf['duration'] = self.ui.protoDurationSpin.value()
        #self.currentProtocol.conf['continuous'] = self.ui.protoContinuousCheck.isChecked()
        #self.currentProtocol.conf['cycleTime'] = self.ui.seqCycleTimeSpin.value()
        #self.currentIsModified(True)
        
    #def currentIsModified(self, v):
        ### Inform the module whether the current protocol is modified from its stored state
        #self.currentProtocol.modified = v
        #if (not v) or (self.currentProtocol.fileName is not None):
            #self.ui.saveProtocolBtn.setEnabled(v)
        
    def newProtocol(self):
        ## Remove all docks
        self.clearDocks()
        
        ## Create new empty protocol object
        self.currentProtocol = Protocol()
        
        self.protoStateGroup.setState({
            'continuous': False,
            'duration': 0.2,
            'leadTime': 0.0,
            'loop': False,
            'loopCycleTime': 1.0,
            'cycleTime': 0.0
        })
        
        self.currentProtocol.conf = self.protoStateGroup.state()
        
        ## Clear extra devices in dev list
        self.updateDeviceList()
        
        self.updateProtParams()
        
        ## Clear sequence parameters, disable sequence dock
        
        self.ui.currentProtocolLabel.setText('[ new ]')
        
        self.ui.saveProtocolBtn.setEnabled(False)
        #self.currentIsModified(False)
    
    def updateProtParams(self, prot=None):
        if prot is None:
            prot = self.currentProtocol
            
        self.protoStateGroup.setState(prot.conf)
        #self.ui.protoDurationSpin.setValue(prot.conf['duration'])
        #if 'cycleTime' in prot.conf:
            #self.ui.seqCycleTimeSpin.setValue(prot.conf['cycleTime'])
        #if prot.conf['continuous']:
            #self.ui.protoContinuousCheck.setCheckState(QtCore.Qt.Checked)
        #else:
            #self.ui.protoContinuousCheck.setCheckState(QtCore.Qt.Unchecked)
    
    def getSelectedFileName(self):
        sel = list(self.ui.protocolList.selectedIndexes())
        if len(sel) == 1:
            index = sel[0]
        else:
            raise Exception("Can not load--%d items selected" % len(sel))
        return self.protocolList.getFileName(index)
    
    def loadProtocol(self, index=None):
        ## Determine selected item
        if index is None:
            sel = list(self.ui.protocolList.selectedIndexes())
            if len(sel) == 1:
                index = sel[0]
            else:
                raise Exception("Can not load--%d items selected" % len(sel))
            
        fn = self.protocolList.getFileName(index)
        
        ## Create protocol object from requested file
        prot = Protocol(fileName=fn)
        
        ## Remove all docks
        self.clearDocks()
        #print "Docks cleared."
        
        ## Update protocol parameters
        self.updateProtParams(prot)
        
        ## update dev list
        self.updateDeviceList(prot)
        
        ## Update sequence parameters, dis/enable sequence dock
        
        ## Create new docks
        self.updateDocks(prot)
        
        ## Set current protocol
        self.currentProtocol = prot
        
        ## Configure docks
        for d in prot.devices:
            if d in self.docks:
                self.docks[d].widget().restoreState(prot.devices[d])
            
        
        ## Configure dock positions
        if 'winState' in prot.conf:
            self.win.restoreState(QtCore.QByteArray.fromPercentEncoding(prot.conf['winState']))
            
        pn = fn.replace(self.protocolList.baseDir, '')
        self.ui.currentProtocolLabel.setText(pn)
        self.ui.saveProtocolBtn.setEnabled(True)
        #self.currentIsModified(False)
    
    def saveProtocol(self, fileName=None):
        ## store window state
        ws = str(self.win.saveState().toPercentEncoding())
        self.currentProtocol.conf['winState'] = ws
        
        ## store individual dock states
        for d in self.docks:
            if self.currentProtocol.deviceEnabled(d):
                self.currentProtocol.devices[d] = self.docks[d].widget().saveState()
        
        ## Write protocol config to file
        self.currentProtocol.write(fileName)
        #self.currentIsModified(False)
        
        ## refresh protocol list
        self.protocolList.clearCache()
    
    def saveProtocolAs(self):
        ## Decide on new file name
        if self.currentProtocol.fileName is not None:
            baseFile = self.currentProtocol.fileName
        else:
            baseFile = os.path.join(self.protocolList.baseDir, 'protocol')
            
        c = 2
        newFile = None
        while True:
            newFile = baseFile + '_%02d' % c
            if not os.path.exists(newFile):
                break
            c += 1
            
        ## write
        self.saveProtocol(newFile)
        
        
        ## Start editing new file name
        index = self.protocolList.findIndex(newFile)
        #self.ui.protocolList.update(index)
        self.ui.protocolList.edit(index)
        
        pn = newFile.replace(self.protocolList.baseDir, '')
        self.ui.currentProtocolLabel.setText(pn)
        self.ui.saveProtocolBtn.setEnabled(True)
        #self.currentIsModified(False)
    
    def deleteProtocol(self):
        ## Delete button must be clicked twice.
        if self.deleteState == 0:
            self.ui.deleteProtocolBtn.setText('Really?')
            self.deleteState = 1
        elif self.deleteState == 1:
            try:
                fn = self.getSelectedFileName()
                os.remove(fn)
                self.protocolList.clearCache()
            except:
                sys.excepthook(*sys.exc_info())
                return
            finally:
                self.resetDeleteState()
    
    def resetDeleteState(self):
        self.deleteState = 0
        self.ui.deleteProtocolBtn.setText('Delete')
    
    def testSingle(self):
        self.runSingle(store=False)
    
    def runSingle(self, store=True):
        ## Disable all start buttons
        self.enableStartBtns(False)
        
        ## Generate executable conf from protocol object
        prot = self.generateProtocol(store)
        
        self.emit(QtCore.SIGNAL('protocolStarted()'))
        self.taskThread.startProtocol(prot)
        
   
    def generateProtocol(self, store, params={}):
        ## params should be in the form {(dev, param): value, ...}
        ## Generate executable conf from protocol object
        #prot = {'protocol': {
            #'duration': self.currentProtocol.conf['duration'], 
            #'storeData': store,
            #'mode': 'single',
            #'name': self.currentProtocol.fileName,
            #'cycleTime': self.currentProtocol.conf['cycleTime'], 
        #}}
        prot = self.protoStateGroup.state()
        prot['storeData'] = store
        prot['name'] = self.currentProtocol.fileName
        
        for d in self.currentProtocol.devices:
            if self.currentProtocol.deviceEnabled(d):
                ## select out just the parameters needed for this device
                p = dict([(i[1], params[i]) for i in params.keys() if i[0] == d])
                ## Ask the device to generate its protocol command
                prot[d] = self.docks[d].widget().generateProtocol(p)
        return prot
        
   
    def testSequence(self):
        self.runSequence(store=False)
       
    def runSequence(self, store=True):
        ## Find all top-level items in the sequence parameter list
        items = []
        for i in range(self.ui.sequenceParamList.topLevelItemCount()):
            items.append(self.ui.sequenceParamList.topLevelItem(i))
        ## Generate parameter space
        params = OrderedDict()
        for i in items:
            key = (str(i.text(0)), str(i.text(1)))
            params[key] = range(int(i.text(2)))
        
        ## Generate the complete array of command structures
        prot = runSequence(lambda p: self.generateProtocol(store, p), params, params.keys(), passHash=True)
        
        self.emit(QtCore.SIGNAL('protocolStarted()'))
        self.taskThread.startProtocol(prot, params)
        
    
    def enableStartBtns(self, v):
        btns = [self.ui.testSingleBtn, self.ui.runProtocolBtn, self.ui.testSequenceBtn, self.ui.runSequenceBtn]
        for b in btns:
            b.setEnabled(v)
            
    def taskThreadStopped(self):
        self.enableStartBtns(True)
    
    def handleFrame(self, frame):
        dataManager = None
        ## Should this data be stored?
        if frame['cmd']['protocol']['storeData']:
            ## Create directory for storing 
            pass
            ## Store protocol command and parameter details
        ## Request each device handles its own data
        for d in frame['result']:
            if d != 'protocol':
                self.docks[d].widget().handleResult(frame['result'][d], dataManager)
    
class Protocol:
    def __init__(self, fileName=None):
        
        if fileName is not None:
            self.name = os.path.split(fileName)[1]
            self.fileName = fileName
            conf = readConfigFile(fileName)
            self.conf = conf['protocol']
            self.devices = conf['devices']
            self.enabled = self.devices.keys()
        else:
            #self.fileName = None
            #self.name = None
            #self.conf = {
                #'devices': {}, 
                #'duration': 0.2, 
                #'continuous': False, 
                #'cycleTime': 0.0
            #}
            self.enabled = []
            self.conf = {}
            self.devices = {}
        
    def generateProtocol(self, **args):
        """Generate the configuration data that will execute this protocol"""
        
        pass
    
    def deviceEnabled(self, dev):
        return dev in self.enabled
        
        
    def write(self, fileName=None):
        conf = self.conf.copy()
        devs = delf.devices.copy()
        
        ## Remove unused devices before writing
        rem = [d for d in devs if not self.deviceEnabled(d)]
        for d in rem:
            del devs[d]
                
        if fileName is None:
            if self.fileName is None:
                raise Exception("Can not write protocol--no file name specified")
            fileName = self.fileName
        self.fileName = fileName
        writeConfigFile({'conf': conf, 'devices': devs}, fileName)
    
    def enabledDevices(self):
        return self.enabled[:]
        
    def removeDevice(self, dev):
        if dev in self.enabled:
            self.enabled.remove(dev)
        
    def addDevice(self, dev):
        if dev not in self.devices:
            self.devices[dev] = {}
        if dev not in self.enabled:
            self.enabled.append(dev)
            
    def renameDevice(self, oldName, newName):
        if oldName not in self.conf['devices']:
            return
        self.devices[newName] = self.devices[oldName]
        del self.devices[oldName]
        if oldName in self.enabled:
            self.enabled.append(newName)
            self.enabled.remove(oldName)
        else:
            if newName in self.enabled:
                self.enabled.remove(newName)
            
class TaskThread(QtCore.QThread):
    def __init__(self, ui):
        QtCore.QThread.__init__(self)
        self.ui = ui
        self.dm = self.ui.manager
        self.lock = QtCore.QMutex(QtCore.QMutex.Recursive)
        self.stopThread = True
                
    def startProtocol(self, protocol, paramSpace=None):
        l = QtCore.QMutexLocker(self.lock)
        if self.isRunning():
            raise Exception("Already running another protocol")
        self.protocol = protocol
        self.paramSpace = paramSpace
        self.lastRunTime = None
        self.start()
    
                
    def run(self):
        try:
            l = QtCore.QMutexLocker(self.lock)
            self.stopThread = False
            l.unlock()
            
            if self.paramSpace is None:
                result = self.runOnce()
            else:
                result = runSequence(self.runOnce, self.paramSpace, self.paramSpace.keys(), passHash=True)
            
        except:
            print "Error in protocol thread, exiting."
            sys.excepthook(*sys.exc_info())
        #finally:
            #self.emit(QtCore.SIGNAL("protocolFinished()"))
                    
    def runOnce(self, params=None):
        ## Select correct command to execute
        cmd = self.protocol
        if params is not None:
            for p in params:
                cmd = cmd[p: params[p]]
                
        ## Todo: wait before starting if we've already run too recently
        while (self.lastRunTime is not None) and (time.clock() < self.lastRunTime + cmd['protocol']['cycleTime']):
            time.sleep(1e-3)
        
        ## Run
        #print "Create task:"
        #print cmd
        task = self.dm.createTask(cmd)
        self.lastRunTime = time.clock()
        task.execute()
            
        ## wait for finish, watch for abort requests
        while True:
            if task.isDone():
                break
            #if self.abort:
                #task.abort()
                #print "Protocol run aborted by user"
                #return
            ## Abort if protocol is taking too long
            #if time.clock() >= (self.lastRunTime+(cmd['protocol']['duration']+0.2)):
                #print "Protocol run aborted--timeout"
                #task.abort()
                #return
            time.sleep(100e-6)
        
        result = task.getResult()
        frame = {'params': params, 'cmd': cmd, 'result': result}
        self.emit(QtCore.SIGNAL('newFrame(PyQt_PyObject)'), frame)
        return result
                    
    def stop(self, block=False):
        l = QtCore.QMutexLocker(self.lock)
        self.stopThread = True
        l.unlock()
        if block:
            self.wait()