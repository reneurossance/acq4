from PyQt4 import QtGui, QtCore
#import configfile
from lib.Manager import getManager, logExc, logMsg
from Mutex import Mutex
from lib.devices.DAQGeneric import DAQGeneric, DAQGenericTask
from LaserDevGui import LaserDevGui
from LaserProtocolGui import LaserProtoGui
import os
import time
import numpy as np
from scipy import stats
from pyqtgraph.functions import siFormat
from HelpfulException import HelpfulException
import pyqtgraph as pg
import metaarray



class Laser(DAQGeneric):
    """The laser device accomplishes a few tasks:
       - Calibration of laser power so that the power at the specimen can be controlled
         (via pockels cell or Q-switch PWM)
       - Immediate recalibration from photocell or internal measurements
       - integrated control of shutters, q-switches, and pockels cells
       - Pulse commands, allowing the user to specify the energy per pulse
       - Control of wavelength and dispersion tuning when available

    Configuration examples:
    
    Laser-blue:
        driver: 'Laser'
        config:
            scope: 'Microscope'
            shutter:
                channel: 'DAQ', '/Dev3/line14'
                delay: 10*ms
            wavelength: 473*nm
            power: 10*mW
            alignmentMode:
                shutter: True
    
    Laser-UV:
        driver: 'Laser'
        config: 
            scope: 'Microscope'
            pulseRate: 100*kHz                      ## Laser's pulse rate
            powerIndicator: 
                channel: 'DAQ', '/Dev1/ai11'      ## photocell channel for immediate recalibration
                rate: 1.2*MHz
                settlingTime: 1*ms
                measurmentTime: 5*ms
            shutter:
                channel: 'DAQ', '/Dev1/line10'    ## channel for triggering shutter
                delay: 10*ms                      ## how long it takes the shutter to fully open
            qSwitch:
                channel: 'DAQ', '/Dev1/line11'    ## channel for triggering q-switch
            wavelength: 355*nm
            alignmentMode:
                qSwitch: False                    ## For alignment, shutter is open but QS is off
                shutter: True
            
    Laser-2p:
        driver: 'CoherentLaser'
        config: 
            serialPort: 6                         ## serial port connected to laser
            scope: 'Microscope'
            pulseRate: 100*kHz                      ## Laser's pulse rate; limits minimum pulse duration
            pCell:
                channel: 'DAQ', '/Dev2/ao1'       ## channel for pockels cell control
            namedWavelengths:
                UV uncaging: 710*nm
                AF488: 976*nm
            alignmentMode:
                pCellVoltage:
                
    Notes: 
        must handle CW (continuous wave), QS (Q-switched), ML (modelocked) lasers
        
        
        
    Protocol examples:
    
    { 'wavelength': 780*nm, 'powerWaveform': array([...]) }  ## calibrated; float array in W
    { 'switchWaveform': array([...]) }                       ## uncalibrated; 0=off -> 1=full power
    { 'pulse': [(0.5*s, 100*uJ), ...] }                     ## (time, pulse energy) pairs
    """
    
    sigPowerChanged = QtCore.Signal(object)
    
    def __init__(self, manager, config, name):
        self.config = config
        self.hasPowerIndicator = False
        self.hasShutter = False
        self.hasTriggerableShutter = False
        self.hasQSwitch = False
        self.hasPCell = False
        self.hasTunableWavelength = False
        
        self.params = {
            'expectedPower': config.get('power', None), ## Expected output
            'currentPower': None, ## Last measured output power
            'scopeTransmission': None, ## percentage of output power that is transmitted to slice
            'tolerance': 5.0, ## in %
            'useExpectedPower': True
            }
        
        daqConfig = {} ### DAQ generic needs to know about powerIndicator, pCell, shutter, qswitch
        if 'powerIndicator' in config:
            self.hasPowerIndicator = True
            #### name of powerIndicator is config['powerIndicator']['channel'][0]
            #daqConfig['powerInd'] = {'channel': config['powerIndicator']['channel'], 'type': 'ai'}
        if 'shutter' in config:
            daqConfig['shutter'] = config['shutter']
            self.hasTriggerableShutter = True
            self.hasShutter = True
        if 'qSwitch' in config:
            daqConfig['qSwitch'] = config['qSwitch']
            self.hasQSwitch = True
        if 'pCell' in config:
            daqConfig['pCell'] = config['pCell']
            self.hasPCell = True
                        
        daqConfig['power'] = {'type': 'ao', 'units': 'W'}  ## virtual channel used for creating control widgets
        DAQGeneric.__init__(self, manager, daqConfig, name)
        
       
        self._configDir = os.path.join('devices', self.name + '_config')
        self.lock = Mutex(QtCore.QMutex.Recursive)
        self.variableLock = Mutex(QtCore.QMutex.Recursive)
        self.calibrationIndex = None
        self.pCellCalibration = None
        self.getPowerHistory()
        
    def configDir(self):
        """Return the name of the directory where configuration/calibration data should be stored"""
        return self._configDir
    
    def setParam(self, **kwargs):
        with self.variableLock:
            for k in kwargs:
                self.params[k] = kwargs[k]
                
    def getParam(self, arg):
        with self.variableLock:
            return self.params[arg]
    
    def getCalibrationIndex(self):
        with self.lock:
            if self.calibrationIndex is None:
                calDir = self.configDir()
                fileName = os.path.join(calDir, 'index')
                index = self.dm.readConfigFile(fileName)
                self.calibrationIndex = index
                self.pCellCalibration = index.get('pCellCalibration', None)
            return self.calibrationIndex
        
    def getPowerHistory(self):
        with self.variableLock:
            fileName = os.path.join(self.configDir(), 'powerHistory')
            if os.path.exists(os.path.join(self.dm.configDir, fileName)):
                ph = self.dm.readConfigFile(fileName)
                self.powerHistoryCount = len(ph)
                self.params['expectedPower'] = ph.values()[-1]['expectedPower']
                return ph
            else:
                date = str(time.strftime('%Y.%m.%d %H:%M:%S'))
                self.dm.writeConfigFile({'entry_0': {'date':date, 'expectedPower':0}}, fileName)
                self.params['expectedPower'] = 0
                self.powerHistoryCount = 1
                
    def appendPowerHistory(self, power):
        with self.variableLock:
            calDir = self.configDir()
            fileName = os.path.join(calDir, 'powerHistory')
            date = str(time.strftime('%Y.%m.%d %H:%M:%S'))
            self.dm.appendConfigFile({'entry_'+str(self.powerHistoryCount):{'date': date, 'expectedPower':power}}, fileName)
            
    

    def writeCalibrationIndex(self, index):
        with self.lock:
            calDir = self.configDir()
            fileName = os.path.join(calDir, 'index')
            self.dm.writeConfigFile(index, fileName)
            #configfile.writeConfigFile(index, fileName)
            self.calibrationIndex = index
        
    def setAlignmentMode(self, b):
        """If true, configures the laser for low-power alignment mode. 
        Note: If the laser achieves low power solely through PWM, then
        alignment mode will only be available during protocols."""
        
        pass
    
    def setWavelength(self, wl):
        """Set the laser's wavelength (if tunable).
        Arguments:
          wl:  """
        raise HelpfulException("%s device does not support wavelength tuning." %str(self.name), reasons=["Hardware doesn't support tuning.", "setWavelenth function is not reimplemented in subclass."])
    
    def getWavelength(self):
        return self.config.get('wavelength', None)
        
    def openShutter(self):
        self.setChanHolding('shutter', 1)
    
    def closeShutter(self):
        self.setChanHolding('shutter', 0)
    
    def openQSwitch(self):
        self.setChanHolding('qSwitch', 1)
    
    def closeQSwitch(self):
        self.setChanHolding('qSwitch', 0)
    
    
    def createTask(self, cmd):
        return LaserTask(self, cmd)
        
    def protocolInterface(self, prot):
        return LaserProtoGui(self, prot)
        
    def deviceInterface(self, win):
        return LaserDevGui(self)
    
    def getDAQName(self, channel=None):
        if channel is None:
            if self.hasTriggerableShutter:
                ch = 'shutter'
                daqName = DAQGeneric.getDAQName(self, 'shutter')
            elif self.hasPCell:
                ch = 'pCell'
                daqName = DAQGeneric.getDAQName(self, 'pCell')
            elif self.hasQSwitch:
                ch = 'qSwitch'
                daqName = DAQGeneric.getDAQName(self, 'qSwitch')
            else:
                raise HelpfulException("LaserTask can't find name of DAQ device to use for this protocol.")
            return (daqName, ch)
        else:
            return DAQGeneric.getDAQName(self, channel)
        
    def runCalibration(self, powerMeter=None, measureTime=0.1, settleTime=0.005, pCellVoltage=None, rate = 100000):
        daqName = self.getDAQName()[0]
        duration = measureTime + settleTime
        nPts = int(rate * duration)
        
        cmdOff = {'protocol': {'duration': duration, 'timeout': duration+5.0},
                self.name: {'shutterMode':'closed', 'switchWaveform':np.zeros(nPts, dtype=np.byte)},
                powerMeter: {x: {'record':True, 'recordInit':False} for x in getManager().getDevice(powerMeter).listChannels()},
                daqName: {'numPts': nPts, 'rate':rate}
                }
        
        if self.hasPowerIndicator:
            powerInd = self.config['powerIndicator']['channel']
            cmdOff[powerInd[0]] = {powerInd[1]: {'record':True, 'recordInit':False}}
            
        if pCellVoltage is not None:
            if self.hasPCell:
                a = np.zeros(nPts, dtype=float)
                a[:] = pCellVoltage
                cmdOff[self.name]['pCell'] = a
            else:
                raise Exception("Laser device %s does not have a pCell, therefore no pCell voltage can be set." %self.dev.name)
            
        cmdOn = cmdOff.copy()
        wave = np.ones(nPts, dtype=np.byte)
        wave[-1] = 0
        shutterDelay = self.config.get('shutter', {}).get('delay', 0)
        wave[:shutterDelay*rate] = 0
        cmdOn[self.name]={'shutterMode':'open', 'switchWaveform':wave}
        
        print "cmdOff: ", cmdOff
        taskOff = getManager().createTask(cmdOff)
        taskOff.execute()
        resultOff = taskOff.getResult()
        
        taskOn = getManager().createTask(cmdOn)
        taskOn.execute()
        resultOn = taskOn.getResult()
            
        measurementStart = (shutterDelay+settleTime)*rate
            
        if self.hasPowerIndicator:
            powerOutOn = resultOn[powerInd[0]][0][measurementStart:].mean()
        else:
            powerOutOn = self.outputPower()
            
        laserOff = resultOff[powerMeter][0][measurementStart:]
        laserOn = resultOn[powerMeter][0][measurementStart:]
                          
        t, prob = stats.ttest_ind(laserOn, laserOff)
        if prob > 0.001:
            raise Exception("Power meter device %s could not detect laser." %powerMeter)
        else:
            powerSampleOn = laserOn.mean()
            transmission = powerSampleOn/powerOutOn
            return (powerSampleOn, transmission)
       
        
    def outputPower(self):
        """Return the current output power of the laser (excluding the effect of pockel cell, shutter, etc.)
        This information is determined in one of a few ways:
           1. The laser directly reports its power output (function needs to be reimplemented in subclass)
           2. A photodiode receves a small fraction of the beam and reports an estimated power
           3. The output power is specified in the config file
        """
        
        if self.hasPowerIndicator:
            ## run a protocol that checks the power
            daqName =  self.getDAQName('shutter')
            powerInd = self.config['powerIndicator']['channel']
            rate = self.config['powerIndicator']['rate']
            
            pConfig = getManager().getDevice(self.config['powerIndicator']['channel'][0]).listChannels()[self.config['powerIndicator']['channel'][1]]
            sTime = pConfig.get('settlingTime', None)
            mTime = pConfig.get('measurementTime', None)
            
            if mTime is None or sTime is None:
                raise Exception("The power indicator (%s) specified for %s needs to be configured with both a 'settlingTime' value and a 'measurementTime' value." %(self.config['powerIndicator']['channel'], self.name))
            
            dur = 0.1 + (sTime+mTime)
            nPts = int(dur*rate)
            
            ### create a waveform that flashes the QSwitch(or other way of turning on) the number specified by reps
            waveform = np.zeros(nPts, dtype=np.byte)
            #for i in range(reps):
                #waveform[(i+1)/10.*rate:((i+1)/10.+sTime+mTime)*rate] = 1 ## divide i+1 by 10 to increment by hundreds of milliseconds
            waveform[0.1*rate:-2] = 1
            
            cmd = {
                'protocol': {'duration': dur},
                self.name: {'switchWaveform':waveform, 'shutterMode':'closed'},
                powerInd[0]: {powerInd[1]: {'record':True, 'recordInit':False}},
                daqName: {'numPts': nPts, 'rate': rate}
            }
            print "outputPowerCmd: ", cmd
            task = getManager().createTask(cmd)
            task.execute()
            result = task.getResult()
            
            ## pull out time that laser was on and off so that power can be measured in each state -- discard the settlingTime around each state change
            #onMask = np.zeros(nPts, dtype=np.byte)
            #offMask = np.zeros(nPts, dtype=np.byte)
            #for i in range(reps):
                #onMask[((i+1)/10+sTime)*rate:((i+1)/10+sTime+mTime)*rate] = 1
                #offMask[(i/10.+2*sTime+mTime)*rate:(i+1/10.)*rate] = 1
            laserOn = result[powerInd[0]][0][0.1*rate:-2]
            laserOff = result[powerInd[0]][0][:0.1*rate]
            
            t, prob = stats.ttest_ind(laserOn, laserOff)
            if prob < 0.01: ### if powerOn is statistically different from powerOff
                powerOn = laserOn.mean()
                powerOff = laserOff.mean()
                #self.devGui.ui.outputPowerLabel.setText(siFormat(powerOn, suffix='W')) ## NO! device does not talk to GUI!
                self.sigPowerChanged.emit(powerOn)
                with self.variableLock:
                    self.params['currentPower'] = powerOn
                    #self.devGui.ui.samplePowerLabel.setText(siFormat(powerOn*self.scopeTransmission, suffix='W'))
                    pmin = self.params['expectedPower'] - self.params['expectedPower']*self.params['tolerance']/100.0
                    pmax = self.params['expectedPower'] + self.params['expectedPower']*self.params['tolerance']/100.0
                if powerOn < pmin or powerOn > pmax:
                    logMsg("%s power is outside expected range. Please adjust expected value or adjust the tuning of the laser." %self.name, msgType='error')
                
                return powerOn
            
            else:
                raise Exception("No laser pulse detected by power indicator '%s' while measuring Laser.outputPower()" % powerInd[0])
            
        ## return the power specified in the config file if there's no powerIndicator
        else:
            return self.config.get('power', None)
        
    def getPCellWaveform(self, powerCmd):
        ### return a waveform of pCell voltages to give the power in powerCmd
        raise Exception("Support for pockel cells is not yet implemented.")
    

    def getChannelCmds(self, cmd, rate):
        ### cmd is a dict and can contain 'powerWaveform' or 'switchWaveform' keys with the array as a value
        
        
        if 'switchWaveform' in cmd:
            cmdWaveform = cmd['switchWaveform']
            vals = np.unique(cmd['switchWaveform'])
            if not self.hasPCell and len(vals)==2 and (1 or 1.0) not in vals: ## check to make sure we can give the specified power.
                raise Exception('An analog power modulator is neccessary to get power values other than zero and one (full power). The following values (as percentages of full power) were requested: %s. This %s device does not have an analog power modulator.' %(str(vals), self.name))
        elif 'powerWaveform' in cmd:
            cmdWaveform = cmd['powerWaveform']
        else:
            raise Exception('Not sure how to generate channel commands for %s' %str(cmd))
        
        nPts = len(cmdWaveform)
        daqCmd = {}
        #if self.dev.config.get('pCell', None) is not None:
        if self.hasPCell:
            ## convert power values using calibration data
            if 'switchWaveform' in cmd:
                with self.variableLock:
                    if self.params['useExpectedPower']:
                        power = self.params['expectedPower']
                    else:
                        power = self.params['currentPower']
                    transmission = self.params['scopeTransmission']
                    #transmission = 0.1
                powerCmd = cmd['switchWaveform']*power*transmission
            else:
                powerCmd = cmd['powerWaveform']
            daqCmd['pCell'] = self.getPCellWaveform(powerCmd)
        else:
            if len(np.unique(cmdWaveform)) > 2: ## check to make sure command doesn't specify powers we can't do
                raise Exception("%s device does not have an analog power modulator, so can only have a binary power command." %str(self.name))
            
        if self.hasQSwitch:
        #if self.dev.config.get('qSwitch', None) is not None:
            qswitchCmd = np.zeros(nPts, dtype=np.byte)
            qswitchCmd[cmdWaveform > 1e-5] = 1
            daqCmd['qSwitch'] = qswitchCmd
            
        if self.hasTriggerableShutter:
            shutterCmd = np.zeros(nPts, dtype=np.byte)
            delay = self.config['shutter'].get('delay', 0.0) 
            shutterCmd[cmdWaveform != 0] = 1 ## open shutter when we expect power
            ## open shutter a little before we expect power because it has a delay
            delayPts = int(delay*rate) 
            a = np.argwhere(shutterCmd[1:]-shutterCmd[:-1] == 1)
            
            if 1 in shutterCmd[:delayPts]:
                raise HelpfulException("Shutter takes %g seconds to open. Power pulse cannot be started before then." %delay)
            for i,x in enumerate(a):
                shutterCmd[a[i]-delayPts:a[i]+1] = 1
            daqCmd['shutter'] = shutterCmd
            
        return daqCmd

            
    def testProtocol(self):
        daqName = self.getDAQName('shutter')
        powerInd = self.config['powerIndicator']['channel']
        rate = self.config['powerIndicator']['rate']
        sTime = self.config['powerIndicator']['settlingTime']
        mTime = self.config['powerIndicator']['measurementTime']
        reps = 10
        dur = 0.1 + reps*0.1+(0.001+0.005)
        nPts = int(dur*rate)
       
        
        
        ### create a waveform that flashes the QSwitch(or other way of turning on) the number specified by reps
        waveform = np.zeros(nPts, dtype=np.byte)
        for i in range(reps):
            waveform[(i+1)/10.*rate:((i+1)/10.+sTime+mTime)*rate] = 1 ## divide i+1 by 10 to increment by hundreds of milliseconds
        
        cmd = {
            'protocol': {'duration': dur},
            self.name: {'switchWaveform':waveform, 'shutterMode':'closed'},
            powerInd[0]: {powerInd[1]: {'record':True, 'recordInit':False}},
            daqName: {'numPts': nPts, 'rate': rate}
        }
        
        task = getManager().createTask(cmd)
        task.execute()
        result = task.getResult()
        
        print "Got result: ", result
        



class LaserTask(DAQGenericTask):
    """
    
    Example protocol command structure:
    {                                  #### powerWaveform, switchWaveform and pulses are mutually exclusive; result of specifying more than one is undefined
        'powerWaveform': array(...),   ## array of power values (specifies the power that should enter the sample)
                                       ## only useful if there is an analog modulator of some kind (Pockel cell, etc)
        'switchWaveform': array(...),  ## array of values 0-1 specifying the fraction of full power to output.
                                       ## For Q-switched lasers, a value > 0 activates the switch
        'pulses': [(time, energy, duration), ...], ### Not yet implemented:
                                       ## the device will generate its own command structure with the requested pulse energies.
                                       ## duration may only be specified if a modulator is available.
                                       
                                       
                                       #####   Specifically specifying the daqGeneric cmd for the qSwitch, pCell or shutter gets precedent over waveforms that might be calculated from a 'powerWaveform', 'switchWaveform', or 'pulses' cmd
        'pCell': {'command':array(....),               ## daqGeneric command that gets passed straight through
                  'preset': value
                  'holding': value}
        'qSwitch: {'command':array(....), etc}         ## array of 0/1 values that specify whether qSwitch is off (0) or on (1)           
        'shutter': {'command':array(....), etc}          ## array of 0/1 values that specify whether shutter is open (1) or closed (0)
        
                                       #### 'shutter' and 'shutterMode' are exclusive; if 'shutter' is specified shutterMode will be ignored
        'shutterMode': 'auto',         ## specifies how the shutter should be used:
                                       ##   auto -- the shutter is opened immediately (with small delay) before laser output is needed
                                       ##           and closed immediately (with no delay) after. Default.
                                       ##   open -- the shutter is opened for the whole protocol and returned to its holding state afterward
                                       ##   closed -- the shutter is kept closed for the protocol and returned to its holding state afterward
                                       
        'wavelength': x,               ## sets the wavelength before executing the protocol
        'checkPower': True,            ## If true, the laser will check its output power before executing the protocol. 
        
    }
    
    """
    def __init__(self, dev, cmd):
        self.cmd = cmd
        self.dev = dev ## this happens in DAQGeneric initialization, but we need it here too since it is used in making the waveforms that go into DaqGeneric.__init__
        if 'shutterMode' not in cmd:
            cmd['shutterMode'] = 'auto'
            
        ## create protocol structure to pass to daqGeneric, and retain a pointer to it here; DAQGeneric protocols will get filled in from LaserTask when self.configure() gets called
        cmd['daqProtocol'] = {}
        if 'shutter' in dev.config:
            cmd['daqProtocol']['shutter'] = {}
        if 'qSwitch' in dev.config:
            cmd['daqProtocol']['qSwitch'] = {}
        if 'pCell' in dev.config:
            cmd['daqProtocol']['pCell'] = {}
            

        DAQGenericTask.__init__(self, dev, cmd['daqProtocol'])
        
    def configure(self, tasks, startOrder):
        ##  Get rate: first get name of DAQ, then ask the DAQ task for its rate
        daqName, ch = self.dev.getDAQName()
        daqTask = tasks[daqName]
        rate = daqTask.getChanSampleRate(self.dev.config[ch]['channel'][1])
        
        ### do a power check if requested
        if self.cmd.get('checkPower', False):
            self.dev.outputPower()
            
        ### set wavelength
        if 'wavelength' in self.cmd:
            self.dev.setWavelength(self.cmd['wavelength'])
            
            
        ### send power/switch waveforms to device for pCell/qSwitch/shutter cmd calculation
        print "Cmd:", self.cmd
        if 'powerWaveform' in self.cmd and not self.cmd.get('ignorePowerWaveform', False):
            calcCmds = self.dev.getChannelCmds({'powerWaveform':self.cmd['powerWaveform']}, rate)
        elif 'switchWaveform' in self.cmd:
            calcCmds = self.dev.getChannelCmds({'switchWaveform':self.cmd['switchWaveform']}, rate)
        elif 'pulse' in self.cmd:
            raise Exception('Support for (pulseTime/energy) pair commands is not yet implemented.')
        else:
            calcCmds = {}

        
        ### set up shutter, qSwitch and pCell -- self.cmd['daqProtocol'] points to the command structure that the DAQGeneric will use, don't set self.cmd['daqProtocol'] equal to something else!
        if 'shutter' in self.cmd:
            self.cmd['daqProtocol']['shutter'] = self.cmd['shutter']
            self.cmd['daqProtocol']['shutter']['command'][-1] = 0
        elif 'shutterMode' in self.cmd:
            if self.cmd['shutterMode'] is 'auto':
                self.cmd['daqProtocol']['shutter']['command']= calcCmds['shutter']
            elif self.cmd['shutterMode'] is 'closed':
                self.cmd['daqProtocol']['shutter']['command'] = np.zeros(len(calcCmds['shutter']), dtype=np.byte)
            elif self.cmd['shutterMode'] is 'open':
                self.cmd['daqProtocol']['shutter']['command'] = np.ones(len(calcCmds['shutter']), dtype=np.byte)
            self.cmd['daqProtocol']['shutter']['command'][-1] = 0
            
        if 'pCell' in self.cmd:
            self.cmd['daqProtocol']['pCell'] = self.cmd['pCell']
        elif 'pCell' in calcCmds:
            self.cmd['daqProtocol']['pCell']['command'] = calcCmds['pCell']
            
        if 'qSwitch' in self.cmd:
            self.cmd['daqProtocol']['qSwitch'] = self.cmd['qSwitch']
            self.cmd['daqProtocol']['qSwitch']['command'][-1] = 0
        elif 'qSwitch' in calcCmds:
            self.cmd['daqProtocol']['qSwitch']['command'] = calcCmds['qSwitch']
            self.cmd['daqProtocol']['qSwitch']['command'][-1] = 0
        
        
        self.currentPower = self.dev.getParam('currentPower')
        self.expectedPower = self.dev.getParam('expectedPower')
        
        DAQGenericTask.configure(self, tasks, startOrder) ## DAQGenericTask will use self.cmd['daqProtocol']

        
    def getResult(self):
        ## getResult from DAQGeneric, then add in command waveform
        result = DAQGenericTask.getResult(self)
        arr = result.view(np.ndarray)
        
        #if 'powerWaveform' in self.cmd:
            #arr = np.append(arr, self.cmd['powerWaveform'][np.newaxis, :], axis=0)
            ##result = np.append(result, self.cmd['powerWaveform'][np.newaxis, :], axis=0)
            #result._info[0]['cols'].append({'name': 'power', 'units': 'W'})
        #elif 'switchWaveform' in self.cmd:
            #arr = np.append(arr, self.cmd['switchWaveform'][np.newaxis, :], axis=0)
            ##result = np.append(result, self.cmd['switchWaveform'][np.newaxis, :], axis=0)
            #result._info[0]['cols'].append({'name': 'switch'})
            
        info = {'currentPower': self.currentPower, 
                'expectedPower': self.expectedPower, 
                'requestedWavelength':self.cmd.get('wavelength', None), 
                'shutterMode':self.cmd['shutterMode'],
                'powerCheckRequested':self.cmd.get('checkPower', False),
                'pulsesCmd': self.cmd.get('pulses', None)
                }
              
        
        result._info[-1]['Laser'] = info
        
        result = metaarray.MetaArray(arr, info=result._info)
        
        self.dev.lastResult = result
       
        return result
    
    #def storeResult(self, dirHandle):
        #pass
