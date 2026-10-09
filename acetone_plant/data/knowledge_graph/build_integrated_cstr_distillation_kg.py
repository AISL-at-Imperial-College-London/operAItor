from __future__ import annotations

import json
from pathlib import Path
from rdflib import Dataset, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL, XSD

OUT = Path('/mnt/data')
GRAPH_URI = URIRef('http://www.hsu-ifa.de/graphs/Integrated_CSTR_Distillation_Simulation')
BASE = Namespace('http://example.org/integrated_cstr_distillation#')
VDI3682 = Namespace('http://www.w3id.org/hsu-aut/VDI3682#')
VDI2206 = Namespace('http://www.w3id.org/hsu-aut/VDI2206#')
CPSMod = Namespace('http://www.hsu-ifa.de/ontologies/CPSMod#')
DIN17359 = Namespace('http://www.hsu-ifa.de/ontologies/DIN17359#')
DINEN61360 = Namespace('http://www.hsu-ifa.de/ontologies/DINEN61360#')
SOSA = Namespace('http://www.w3.org/ns/sosa/')
UML = Namespace('http://www.hsu-ifa.de/ontologies/UMLStateMachine#')
INT = Namespace('http://example.org/integrated_cstr_distillation/ontology#')

# ---------- RDF helpers ----------
ds = Dataset()
g = ds.graph(GRAPH_URI)
for pfx, ns in [('int', BASE), ('ip', INT), ('VDI3682', VDI3682), ('VDI2206', VDI2206),
                ('CPSMod', CPSMod), ('DIN17359', DIN17359), ('DINEN61360', DINEN61360),
                ('sosa', SOSA), ('UML', UML), ('rdf', RDF), ('rdfs', RDFS), ('owl', OWL), ('xsd', XSD)]:
    ds.bind(pfx, ns)

def U(local: str) -> URIRef:
    return BASE[local]

def add(uri: URIRef, rdf_types=(), label: str | None = None, comment: str | None = None):
    if isinstance(rdf_types, URIRef): rdf_types = (rdf_types,)
    for t in rdf_types: g.add((uri, RDF.type, t))
    if label is not None: g.add((uri, RDFS.label, Literal(label, lang='en')))
    if comment is not None: g.add((uri, RDFS.comment, Literal(comment, lang='en')))
    return uri

def link(s, p, *objs):
    for o in objs: g.add((s, p, o))

def lit(s, p, value, datatype=None, lang=None):
    g.add((s, p, Literal(value, datatype=datatype, lang=lang)))

def source(s, file: str, symbol: str | None = None):
    lit(s, INT.sourceFile, file)
    if symbol: lit(s, INT.sourceSymbol, symbol)

# ---------- custom ontology terms (small extension for concepts absent from reference vocabularies) ----------
custom_classes = {
    'SafetyInterlock': 'Independent safety instrumented function / trip rule',
    'Alarm': 'Operator alarm definition',
    'OperatorAction': 'Action available through the restricted operator/agent interface',
    'OperatorAuthorityModel': 'Machine-readable boundary of permitted supervisory authority',
    'PumpTrain': 'Duty/standby pump service',
    'CommandStage': 'Stage in the requested-to-actual actuator command chain',
    'OverrideRule': 'Safety override applied to a manipulated variable',
    'Disturbance': 'Exogenous normal process disturbance',
    'FaultMode': 'Parameterized physical, actuator, sensor, pump, utility, or process fault',
    'EpisodeOutcome': 'Episode outcome state',
    'ExogenousProperty': 'Property supplied by the environment rather than normal operator authority',
}
for name, desc in custom_classes.items():
    add(INT[name], OWL.Class, name, desc)

obj_props = {
    'monitorsParameter':'Links alarm/SIF/controller to a monitored parameter',
    'controlsParameter':'Links controller/action to a manipulated parameter',
    'hasSetpoint':'Links a controller to its setpoint reference value',
    'hasAlarm':'Links a system or parameter to an alarm',
    'hasInterlock':'Links a system to an independent safety interlock',
    'hasOverrideRule':'Links a safety interlock to an override rule',
    'overrideTarget':'Manipulated parameter affected by an override rule',
    'hasPrimaryPump':'Primary pump in a duty/standby train',
    'hasStandbyPump':'Standby pump in a duty/standby train',
    'servesParameter':'Flow/manipulated parameter served by a pump train',
    'allowsAction':'Operator authority model allows an action',
    'actsOn':'Operator action target',
    'causesSymptom':'Fault-to-symptom relation',
    'affectsParameter':'Fault/disturbance affects a parameter',
    'hasOutcomeState':'State machine/system has outcome state',
    'hasCommandStage':'System contains a command stage',
    'stageSuccessor':'Next stage in command arbitration chain',
    'relatedSIF':'Alarm related to a corresponding safety trip',
    'implementedBy':'Logical item implemented by physical/software resource',
}
for name, desc in obj_props.items():
    add(INT[name], OWL.ObjectProperty, name, desc)

data_props = {
    'sourceFile':'Source code file from which this KG assertion was derived',
    'sourceSymbol':'Source code symbol from which this KG assertion was derived',
    'limitValue':'Alarm or trip limit', 'direction':'HIGH or LOW comparison direction',
    'priority':'Operator alarm priority', 'hysteresis':'Alarm hysteresis',
    'operator':'Override operator: set/min/max', 'commandValue':'Override command value',
    'plantTrip':'Whether the SIF contributes to the plant-trip latch',
    'setpointMinimum':'Minimum operator-accessible setpoint', 'setpointMaximum':'Maximum operator-accessible setpoint',
    'mvMinimum':'Minimum manipulated-variable command', 'mvMaximum':'Maximum manipulated-variable command',
    'unit':'Engineering unit', 'mode':'Fault/action mode', 'nominalValue':'Nominal value',
    'allowedMinimum':'Operator/action lower bound', 'allowedMaximum':'Operator/action upper bound',
    'description':'Short machine-readable description', 'version':'Model/software version',
    'guardExpression':'Transition guard expression', 'faultKind':'Fault category',
    'prohibitsProperty':'Property outside normal operator/agent authority',
}
for name, desc in data_props.items():
    add(INT[name], OWL.DatatypeProperty, name, desc)

# ---------- system / process ----------
system = add(U('IntegratedPlant_System'), VDI2206.System,
             'Integrated CSTR + Distillation Plant',
             'v0.5.3 synthetic abnormal-operation benchmark: liquid CSTR converting IPA to acetone + H2, followed by a 10-stage binary distillation column with regulatory control, alarms, SIFs, operator actions, and fault layers.')
source(system, 'README.md', 'Integrated CSTR + distillation autonomous-operation simulator — v0.5.3 prototype')
lit(system, INT.version, '0.5.3')
process = add(U('IntegratedPlant_Process'), VDI3682.Process, 'Integrated Reaction and Distillation Process',
              'Fresh IPA is reacted in the CSTR; the liquid reactor effluent feeds tray 6 of a 10-stage acetone/IPA column. Hydrogen is separately removed through PCV/BDV/PSV paths. Column overhead is condensed into a reflux drum and split into reflux and distillate; bottoms leave through the reboiler.')
source(process, 'integrated_plant/plant.py', 'IntegratedPlantDAE')

# Modules
modules = {
 'module_Feed':('Feed and Throughput Module','Fresh IPA feed and P-101 duty/standby service.'),
 'module_CSTR':('CSTR Reactor Module','Liquid CSTR, heating, reaction, level/temperature/pressure states.'),
 'module_H2Protection':('Hydrogen Pressure Protection Module','Normal PCV, emergency BDV and passive PSV H2 removal paths.'),
 'module_Column':('Distillation Column Module','Ten equilibrium stages; feed enters tray 6 from the top.'),
 'module_CondenserDrum':('Condenser and Reflux Drum Module','Condenser cooling capacity, lumped column pressure, reflux drum and reflux/distillate withdrawal.'),
 'module_Reboiler':('Reboiler Module','Steam-heated reboiler with bottoms withdrawal and liquid leak term.'),
 'module_Products':('Product Withdrawal Module','Distillate and bottoms product pump services.'),
 'module_Control':('Regulatory Control Module','Eight sampled PI loops with AUTO/MANUAL, clamping, deadband and bumpless tracking.'),
 'module_Safety':('Safety and Alarm Module','Independent latched SIF layer plus operator alarm state machine.'),
 'module_OperatorInterface':('Operator / Agent Interface Module','Restricted supervisory action interface and command arbitration stages.'),
}
for mid,(label,comment) in modules.items():
    m=add(U(mid), VDI2206.Module,label,comment); link(system, VDI2206.SystemConsistsOfModule, m)

# ---------- parameters / information ----------
# Nominal values obtained from running IntegratedPlantDAE.nominal_state() and RegulatoryControlSystem.from_steady_state().
primary_obs = {
 'cstr_holdup_kmol':('CSTR liquid holdup',50.0,'kmol','module_CSTR'),
 'cstr_temperature_K':('CSTR temperature',335.7416402201051,'K','module_CSTR'),
 'cstr_pressure_bar':('CSTR H2 pressure',3.0,'bar','module_CSTR'),
 'cstr_x_acetone':('CSTR acetone mole fraction',0.70,'mol frac','module_CSTR'),
 'cstr_h2_vent_kmol_h':('Total H2 removal flow',70.0,'kmol/h','module_H2Protection'),
 'reboiler_holdup_kmol':('Reboiler liquid holdup',34.509631253467084,'kmol','module_Reboiler'),
 'drum_holdup_kmol':('Reflux drum liquid holdup',30.24518437326646,'kmol','module_CondenserDrum'),
 'top_temperature_K':('Top tray temperature',331.7216115988592,'K','module_Column'),
 'bottom_temperature_K':('Reboiler / bottom temperature',354.42228104157755,'K','module_Reboiler'),
 'column_pressure_bar':('Lumped condenser / column pressure',1.0,'bar','module_CondenserDrum'),
 'top_pressure_bar':('Top tray pressure',1.0046485453535037,'bar','module_Column'),
 'reboiler_pressure_bar':('Reboiler pressure',1.0440133659626392,'bar','module_Reboiler'),
 'distillate_x_acetone':('Distillate acetone mole fraction',0.9416968951186497,'mol frac','module_CondenserDrum'),
 'bottoms_x_acetone':('Bottoms acetone mole fraction',0.03974394764126594,'mol frac','module_Reboiler'),
}
tray_nom_T=[331.7216115988592,333.37738868222436,334.9602562482823,336.32498367710286,337.40479422918804,338.20810848330717,341.1814788574808,345.09937051415955,349.1624713275198,352.3864826367744]
tray_nom_P=[1.0046485453535037,1.0092264286958201,1.0136834380706392,1.0180386774867054,1.0223163315131973,1.0265392173143684,1.0305315579829568,1.0342351689726583,1.0376593779070038,1.0408881375250272]
for i in range(10):
    primary_obs[f'tray_{i+1:02d}_temperature_K']=(f'Tray {i+1:02d} temperature',tray_nom_T[i],'K','module_Column')
    primary_obs[f'tray_{i+1:02d}_pressure_bar']=(f'Tray {i+1:02d} pressure',tray_nom_P[i],'bar','module_Column')

mv_defs = {
 'fresh_ipa_feed':('Fresh IPA feed / throughput',100.0,'kmol/h','module_Feed'),
 'cstr_outflow':('CSTR liquid outflow / column feed',100.0,'kmol/h','module_CSTR'),
 'cstr_heater_factor':('CSTR heater factor',0.6076043846119756,'fraction','module_CSTR'),
 'cstr_vent_valve':('H2 PCV opening',0.5,'fraction','module_H2Protection'),
 'cstr_bdv_valve':('H2 emergency BDV opening',0.0,'fraction','module_H2Protection'),
 'cstr_emergency_cooling_factor':('Emergency reactor cooling factor',0.0,'fraction','module_CSTR'),
 'reflux_flow':('Reflux flow',80.0,'kmol/h','module_CondenserDrum'),
 'steam_factor':('Reboiler steam duty factor',1.0148912356226403,'fraction','module_Reboiler'),
 'bottoms_flow':('Bottoms flow',26.797062506934168,'kmol/h','module_Products'),
 'distillate_flow':('Distillate flow',73.20293749306585,'kmol/h','module_Products'),
 'condenser_cooling_valve':('Condenser cooling-water valve opening',0.60,'fraction','module_CondenserDrum'),
}
exogenous_defs = {
 'fresh_feed_temperature_K':('Fresh-feed temperature',298.15,'K'),
 'catalyst_activity':('Catalyst activity multiplier',1.0,'fraction'),
 'condenser_cooling_effectiveness':('Condenser cooling effectiveness multiplier',1.0,'fraction'),
 'cooling_water_temperature_K':('Cooling-water temperature',298.15,'K'),
 'steam_pressure_bar':('Steam-header pressure',5.659806566043441,'bar'),
 'cstr_heating_utility_temperature_K':('CSTR heating-utility temperature',390.0,'K'),
 'ambient_temperature_K':('Ambient temperature',298.15,'K'),
 'condenser_pressure_bar':('Legacy condenser reference pressure',1.0,'bar'),
 'cstr_liquid_leak_kmol_h':('CSTR liquid leak rate',0.0,'kmol/h'),
 'reboiler_liquid_leak_kmol_h':('Reboiler liquid leak rate',0.0,'kmol/h'),
 'drum_liquid_leak_kmol_h':('Reflux drum liquid leak rate',0.0,'kmol/h'),
}

param_uri={}; info_uri={}

def add_data_bundle(subject: URIRef, code: str, name: str, unit: str, value=None, definition: str | None=None):
    de=U('DE_'+code); td=U('TD_'+code); ins=U('ID_'+code)
    add(de, DINEN61360.Data_Element, f'Data element {code}')
    add(td, DINEN61360.Type_Description, f'Type description {code}')
    add(ins, DINEN61360.Instance_Description, f'Instance description {code}')
    link(subject, DINEN61360.has_Data_Element, de); link(de,DINEN61360.has_Type_Description,td); link(de,DINEN61360.has_Instance_Description,ins)
    lit(td,DINEN61360.Code,code); lit(td,DINEN61360.Definition,definition or name,lang='en'); lit(td,DINEN61360.Preferred_Name,name,lang='en'); lit(td,DINEN61360.Short_Name,code)
    lit(td,DINEN61360.Unit_of_Measure,unit); lit(td,DINEN61360.Version_Number,'1.0'); lit(td,DINEN61360.Expression_Goal,'Actual value'); lit(td,DINEN61360.Logic_Interpretation,'=')
    if value is not None: lit(ins,DINEN61360.Value,float(value),XSD.double)

for tag,(label,val,unit,mod) in primary_obs.items():
    p=add(U('obs_param_'+tag), (DIN17359.Parameter,SOSA.ObservableProperty), 'obs_param_'+tag+' – '+label,
          f'Observable process property exposed by IntegratedPlantDAE.measurements(): {tag}.')
    i=add(U('information_'+tag), VDI3682.Information, label, f'Measured/observable information tag: {tag}.')
    link(i,CPSMod.isCharacterizedByParameter,p); lit(p,INT.nominalValue,float(val),XSD.double); lit(p,INT.unit,unit)
    source(p,'integrated_plant/plant.py','IntegratedPlantDAE.measurements')
    add_data_bundle(i,tag,label,unit,val)
    param_uri[tag]=p; info_uri[tag]=i

for tag,(label,val,unit,mod) in mv_defs.items():
    p=add(U('act_param_'+tag), (DIN17359.Parameter,SOSA.ActuatableProperty), 'act_param_'+tag+' – '+label,
          f'Manipulated plant input field: {tag}. Requested, supervisory, safety-commanded, and actual values are distinguished by the episode runner.')
    i=add(U('information_'+tag), VDI3682.Information,label,f'Manipulated variable / plant input: {tag}.')
    link(i,CPSMod.isCharacterizedByParameter,p); lit(p,INT.nominalValue,float(val),XSD.double); lit(p,INT.unit,unit)
    source(p,'integrated_plant/plant.py','PlantInputs')
    add_data_bundle(i,tag,label,unit,val)
    param_uri[tag]=p; info_uri[tag]=i

for tag,(label,val,unit) in exogenous_defs.items():
    p=add(U('exo_param_'+tag),(DIN17359.Parameter,INT.ExogenousProperty),'exo_param_'+tag+' – '+label,
          f'Exogenous/environment or fault-injection property: {tag}; not part of normal operator/agent authority.')
    i=add(U('information_'+tag),VDI3682.Information,label,f'External scenario input: {tag}.')
    link(i,CPSMod.isCharacterizedByParameter,p); lit(p,INT.nominalValue,float(val),XSD.double); lit(p,INT.unit,unit)
    source(p,'integrated_plant/plant.py','PlantInputs')
    add_data_bundle(i,tag,label,unit,val)
    param_uri[tag]=p; info_uri[tag]=i

# ---------- products and energy flows ----------
products = {
 'product_fresh_IPA':('Fresh IPA Feed','Fresh liquid isopropanol feed to the reactor.'),
 'product_cstr_liquid':('CSTR Liquid Effluent','Reactor liquid containing acetone and unconverted IPA; feeds tray 6 of the column.'),
 'product_H2':('Hydrogen Gas','Hydrogen produced by the endothermic IPA dehydrogenation reaction.'),
 'product_overhead_vapor':('Column Overhead Vapour','Top-stage vapour sent to condenser.'),
 'product_condensate':('Condenser Condensate','Condensed overhead accumulated in reflux drum.'),
 'product_distillate':('Acetone-rich Distillate','Nominal distillate acetone mole fraction about 0.942.'),
 'product_bottoms':('IPA-rich Bottoms','Nominal bottoms acetone mole fraction about 0.040.'),
 'product_h2_to_flare':('H2 to Flare/Header','Hydrogen removed through PCV, BDV and PSV paths.'),
}
for k,(l,c) in products.items(): add(U(k),VDI3682.Product,l,c)
energies = {
 'energy_feed_thermal':('Feed sensible energy','Fresh-feed thermal condition.'),
 'energy_cstr_heating':('CSTR heating utility','Normal reactor heating jacket duty.'),
 'energy_emergency_cooling':('Emergency reactor cooling','Independent emergency cooling path, normally off.'),
 'energy_reboiler_steam':('Reboiler steam','Steam-header heat supplied to the reboiler.'),
 'energy_condenser_cooling':('Condenser cooling water','Cooling-water duty available to the condenser.'),
}
for k,(l,c) in energies.items(): add(U(k),VDI3682.Energy,l,c)
link(process,VDI3682.hasInput,U('product_fresh_IPA'),U('energy_feed_thermal'),U('energy_cstr_heating'),U('energy_reboiler_steam'),U('energy_condenser_cooling'))
link(process,VDI3682.hasOutput,U('product_distillate'),U('product_bottoms'),U('product_h2_to_flare'))

# ---------- equipment ----------
components={}
def component(cid,label,module,types=(VDI2206.Component,),comment=None, faults=()):
    u=add(U(cid),types,label,comment); link(U(module),VDI2206.ModuleConsistsOfComponent,u); components[cid]=u
    for f in faults: link(u,DIN17359.DiagnosticSubjectHasFault,U(f))
    return u

# Core physical equipment
component('R-101','R-101 CSTR Reactor','module_CSTR',(VDI2206.Component,DIN17359.DiagnosticSubject),'6 m3 liquid vessel; nominal holdup 50 kmol; endothermic IPA -> acetone + H2 benchmark reaction.')
component('E-101','CSTR Heating Jacket','module_CSTR',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Normal heating path, UA = 165000 kJ/h/K, manipulated by cstr_heater_factor.')
component('E-102','Emergency Reactor Cooling Path','module_CSTR',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Independent emergency cooling path, UA = 50000 kJ/h/K, normally off.')
component('PCV-101','H2 Pressure-Control Valve (PCV)','module_H2Protection',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Normal H2 pressure-control final element; manipulated by R_PIC.')
component('BDV-101','H2 Emergency Blowdown Valve (BDV)','module_H2Protection',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Independent actively commanded emergency H2 blowdown valve.')
component('PSV-101','H2 Passive Relief Valve (PSV)','module_H2Protection',(VDI2206.Component,DIN17359.DiagnosticSubject),'Passive low-order relief surrogate; set pressure 4.50 bar, fully open by 4.70 bar.')
component('C-201','C-201 Binary Distillation Column','module_Column',(VDI2206.Component,DIN17359.DiagnosticSubject),'10 equilibrium stages; feed enters zero-based stage 5 (tray 6 from top).')
for i in range(10):
    component(f'C-201-TRAY-{i+1:02d}',f'C-201 Tray {i+1:02d}','module_Column',(VDI2206.Component,),f'Equilibrium stage {i+1} of 10.' + (' Feed tray.' if i==5 else ''))
component('E-201','E-201 Overhead Condenser','module_CondenserDrum',(VDI2206.Component,DIN17359.DiagnosticSubject),'Condenser duty limited by cooling-water valve opening, utility effectiveness, and top-to-CW temperature difference.')
component('CV-201','Condenser Cooling-Water Valve','module_CondenserDrum',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Column pressure manipulated variable; nominal opening 60%.')
component('V-201','V-201 Reflux Drum','module_CondenserDrum',(VDI2206.Component,DIN17359.DiagnosticSubject),'Overhead condensate accumulator feeding reflux and distillate withdrawal.')
component('E-202','E-202 Reboiler','module_Reboiler',(VDI2206.Component,DIN17359.DiagnosticSubject),'Steam-heated reboiler with dynamic holdup and physical liquid leak term.')
component('FV-202','Reboiler Steam Final Element','module_Reboiler',(VDI2206.Actuator,DIN17359.DiagnosticSubject),'Manipulated by BOT_TIC through steam_factor.')

# Pump trains and pumps
pump_defs={
 'FEED':('fresh_ipa_feed','P-101A','P-101B','Fresh IPA feed pump','module_Feed'),
 'CSTR_OUTLET':('cstr_outflow','P-102A','P-102B','CSTR outlet / column feed pump','module_CSTR'),
 'REFLUX':('reflux_flow','P-201A','P-201B','Reflux pump','module_CondenserDrum'),
 'DISTILLATE':('distillate_flow','P-202A','P-202B','Distillate product pump','module_Products'),
 'BOTTOMS':('bottoms_flow','P-203A','P-203B','Bottoms product pump','module_Products'),
}
for tid,(field,pa,pb,desc,mod) in pump_defs.items():
    tr=add(U('pump_train_'+tid),INT.PumpTrain,f'{tid} Pump Train',desc+'; A is primary and B is standby at reset.')
    source(tr,'integrated_plant/equipment.py','PUMP_TRAINS')
    a=component(pa,pa+' – '+desc+' A',mod,(VDI2206.Actuator,DIN17359.DiagnosticSubject),desc+' primary pump.')
    b=component(pb,pb+' – '+desc+' B',mod,(VDI2206.Actuator,DIN17359.DiagnosticSubject),desc+' standby pump.')
    link(tr,INT.hasPrimaryPump,a); link(tr,INT.hasStandbyPump,b); link(tr,INT.servesParameter,param_uri[field])

# Connectivity
connect=VDI2206.isConnectedWith
links=[
 ('P-101A','R-101'),('P-101B','R-101'),('R-101','P-102A'),('R-101','P-102B'),('R-101','PCV-101'),('R-101','BDV-101'),('R-101','PSV-101'),('R-101','E-101'),('R-101','E-102'),
 ('P-102A','C-201'),('P-102B','C-201'),('C-201','E-201'),('C-201','E-202'),('E-201','V-201'),('V-201','P-201A'),('V-201','P-201B'),('V-201','P-202A'),('V-201','P-202B'),('E-202','P-203A'),('E-202','P-203B'),('E-201','CV-201'),('E-202','FV-202')]
for a,b in links: link(U(a),connect,U(b)); link(U(b),connect,U(a))
for i in range(9): link(U(f'C-201-TRAY-{i+1:02d}'),connect,U(f'C-201-TRAY-{i+2:02d}'))
link(U('C-201'),VDI2206.consists_of,*[U(f'C-201-TRAY-{i+1:02d}') for i in range(10)])

# ---------- sensors ----------
sensor_for={
 'cstr_holdup_kmol':('LT-101','Reactor Level / Holdup Sensor','R-101'),
 'cstr_temperature_K':('TT-101','Reactor Temperature Sensor','R-101'),
 'cstr_pressure_bar':('PT-101','Reactor Pressure Sensor','R-101'),
 'cstr_x_acetone':('AIT-101','Reactor Acetone Composition Analyzer','R-101'),
 'cstr_h2_vent_kmol_h':('FT-101H2','Hydrogen Vent Flow Sensor','PCV-101'),
 'reboiler_holdup_kmol':('LT-202','Reboiler Level / Holdup Sensor','E-202'),
 'drum_holdup_kmol':('LT-201','Reflux Drum Level / Holdup Sensor','V-201'),
 'top_temperature_K':('TT-201','Column Top Temperature Sensor','C-201'),
 'bottom_temperature_K':('TT-202','Column Bottom Temperature Sensor','E-202'),
 'column_pressure_bar':('PT-201','Column / Condenser Pressure Sensor','E-201'),
 'top_pressure_bar':('PT-201T','Top Tray Pressure Sensor','C-201'),
 'reboiler_pressure_bar':('PT-202','Reboiler Pressure Sensor','E-202'),
 'distillate_x_acetone':('AIT-201D','Distillate Acetone Analyzer','V-201'),
 'bottoms_x_acetone':('AIT-202B','Bottoms Acetone Analyzer','E-202'),
}
for tag,(sid,label,target) in sensor_for.items():
    # Assign based on target module
    mod='module_Control'
    if target in ('R-101','PCV-101'): mod='module_CSTR' if target=='R-101' else 'module_H2Protection'
    elif target in ('E-202',): mod='module_Reboiler'
    elif target in ('V-201','E-201'): mod='module_CondenserDrum'
    elif target=='C-201': mod='module_Column'
    s=component(sid,label,mod,(VDI2206.Sensor,DIN17359.DiagnosticSubject,SOSA.Sensor),f'Sensor abstraction for measurement tag {tag}; Gaussian noise is applied by MeasurementModel.')
    link(s,connect,U(target)); link(param_uri[tag],SOSA.isObservedBy,s); link(info_uri[tag],CPSMod.isObservedBySensor,s)
    source(s,'integrated_plant/measurement.py','MeasurementModel')
# tray T/P sensors
for i in range(10):
    for kind,sfx in [('temperature_K','TT'),('pressure_bar','PT')]:
        tag=f'tray_{i+1:02d}_{kind}'; sid=f'{sfx}-201-{i+1:02d}'; s=component(sid,f'Tray {i+1:02d} {"Temperature" if sfx=="TT" else "Pressure"} Sensor','module_Column',(VDI2206.Sensor,SOSA.Sensor),f'Measurement tag {tag}.')
        link(s,connect,U(f'C-201-TRAY-{i+1:02d}')); link(param_uri[tag],SOSA.isObservedBy,s); link(info_uri[tag],CPSMod.isObservedBySensor,s)

# ---------- setpoints and controllers ----------
loops={
 'R_LIC':('Reactor Level Controller','cstr_holdup_kmol','cstr_outflow',50.0,25.0,68.0,'kmol',-2.0,0.20,0.0,160.0,0.50,'module_CSTR','LT-101','P-102A'),
 'R_TIC':('Reactor Temperature Controller','cstr_temperature_K','cstr_heater_factor',335.7416402201051,325.0,345.0,'K',0.030,0.12,0.0,1.0,0.30,'module_CSTR','TT-101','E-101'),
 'R_PIC':('Reactor Pressure Controller','cstr_pressure_bar','cstr_vent_valve',3.0,2.5,3.4,'bar',-0.10,0.30,0.0,1.0,0.015,'module_H2Protection','PT-101','PCV-101'),
 'RB_LIC':('Reboiler Level Controller','reboiler_holdup_kmol','bottoms_flow',34.509631253467084,18.0,48.0,'kmol',-2.0,0.25,0.0,90.0,0.35,'module_Reboiler','LT-202','P-203A'),
 'DR_LIC':('Reflux Drum Level Controller','drum_holdup_kmol','distillate_flow',30.24518437326646,16.0,42.0,'kmol',-4.0,0.20,0.0,130.0,0.30,'module_CondenserDrum','LT-201','P-202A'),
 'COL_PIC':('Column Pressure Controller','column_pressure_bar','condenser_cooling_valve',1.0,0.90,1.15,'bar',-1.5,0.10,0.0,1.0,0.002,'module_CondenserDrum','PT-201','CV-201'),
 'TOP_TIC':('Top Temperature / Reflux Controller','top_temperature_K','reflux_flow',331.7216115988592,325.0,340.0,'K',-7.0,0.15,0.0,180.0,0.30,'module_CondenserDrum','TT-201','P-201A'),
 'BOT_TIC':('Bottom Temperature / Steam Controller','bottom_temperature_K','steam_factor',354.42228104157755,345.0,365.0,'K',0.055,0.15,0.0,1.8,0.30,'module_Reboiler','TT-202','FV-202'),
}
controller_uri={}; setpoint_uri={}
for loop,(label,pv,mv,sp,spmin,spmax,spunit,kp,ti,umin,umax,deadband,mod,sensor,actuator) in loops.items():
    ctrl=component('controller_'+loop,label,'module_Control',(VDI2206.InformationProcessing,),f'Sampled PI controller {loop}; supports AUTO/MANUAL and bumpless transfer.')
    controller_uri[loop]=ctrl
    po=add(U('PO_'+loop),VDI3682.ProcessOperator,'PO – '+label,f'Regulatory loop {loop}: PV={pv}, MV={mv}.')
    link(process,VDI3682.consistsOf,po); link(po,VDI3682.isAssignedTo,U(mod)); link(po,VDI3682.hasInput,info_uri[pv]); link(po,VDI3682.hasOutput,info_uri[mv])
    ref=add(U('ref_'+loop+'_SP'),DIN17359.ReferenceValue,loop+' Setpoint',f'Nominal setpoint established from the integrated steady state; operator-accessible bounds are enforced by LoopAccessSpec.')
    lit(ref,DIN17359.referenceValue,float(sp),XSD.double); lit(ref,DIN17359.referenceUnit,spunit); lit(ref,DIN17359.referenceConditions,'Nominal integrated steady state',lang='en'); lit(ref,INT.setpointMinimum,float(spmin),XSD.double); lit(ref,INT.setpointMaximum,float(spmax),XSD.double)
    setpoint_uri[loop]=ref
    spi=add(U('information_'+loop+'_sp'),VDI3682.Information,loop+' Setpoint Information',f'Setpoint information for {loop}.'); link(spi,OWL.sameAs,ref); add_data_bundle(spi,loop+'_sp',loop+' setpoint',spunit,sp)
    link(po,VDI3682.hasInput,spi); link(ctrl,INT.monitorsParameter,param_uri[pv]); link(ctrl,INT.controlsParameter,param_uri[mv]); link(ctrl,INT.hasSetpoint,ref); link(ctrl,connect,U(sensor),U(actuator))
    lit(ctrl,INT.setpointMinimum,float(spmin),XSD.double); lit(ctrl,INT.setpointMaximum,float(spmax),XSD.double); lit(ctrl,INT.mvMinimum,float(umin),XSD.double); lit(ctrl,INT.mvMaximum,float(umax),XSD.double); lit(ctrl,INT.unit,spunit)
    model=add(U('Equation_'+loop),VDI2206.MathematicalModel,f'{loop} PI control law',f'u = clip(bias + Kp*(SP-PV) + integral, u_min, u_max); integral += Kp/Ti*(SP-PV)*dt outside deadband; AUTO/MANUAL transfer uses tracking/back-calculation. Kp={kp:g}, Ti={ti:g} h, deadband={deadband:g}.')
    link(po,CPSMod.processRealizesBehavior,model); link(U('module_Control'),CPSMod.technicalResourceRealizes,model); source(ctrl,'integrated_plant/control.py','RegulatoryControlSystem.LOOP_SPECS'); source(model,'integrated_plant/control.py','PIController.output')

# ---------- physical process operators / models ----------
def procop(pid,label,module,inputs,outputs,comment,eqs):
    po=add(U(pid),VDI3682.ProcessOperator,label,comment); link(process,VDI3682.consistsOf,po); link(po,VDI3682.isAssignedTo,U(module))
    for x in inputs: link(po,VDI3682.hasInput,x)
    for x in outputs: link(po,VDI3682.hasOutput,x)
    for eid,elabel,ecomment in eqs:
        e=add(U(eid),VDI2206.MathematicalModel,elabel,ecomment); link(po,CPSMod.processRealizesBehavior,e); link(U(module),CPSMod.technicalResourceRealizes,e)
    return po

po_feed=procop('PO_FreshFeed','PO – Fresh IPA Feed','module_Feed',[U('product_fresh_IPA'),info_uri['fresh_ipa_feed'],info_uri['fresh_feed_temperature_K']],[U('product_cstr_liquid')],'P-101 duty/standby service delivers the fresh-feed request unless the selected pump is failed.',[
 ('Equation_FeedPump','Feed pump transmission','actual fresh_ipa_feed = requested fresh_ipa_feed unless selected P-101 pump is failed, in which case actual flow = 0.')])
po_cstr=procop('PO_CSTR_Dynamics','PO – CSTR Reaction and Balances','module_CSTR',[U('product_cstr_liquid'),info_uri['fresh_ipa_feed'],info_uri['cstr_outflow'],info_uri['cstr_heater_factor'],info_uri['catalyst_activity'],info_uri['fresh_feed_temperature_K'],info_uri['ambient_temperature_K']],[U('product_cstr_liquid'),U('product_H2'),info_uri['cstr_holdup_kmol'],info_uri['cstr_temperature_K'],info_uri['cstr_x_acetone']],'Direct-residual DAE for endothermic IPA dehydrogenation with liquid mass/component/energy and H2 inventory balances.',[
 ('Equation_CSTR_Rate','CSTR reaction rate','rate = catalyst_activity * k_ref * exp[-E/R*(1/T - 1/T_ref)] * positive(M_liq - n_acetone).'),
 ('Equation_CSTR_Mass','CSTR liquid balance','dM/dt = feed_flow - (liquid_outflow + max(liquid_leak_flow,0)).'),
 ('Equation_CSTR_Acetone','CSTR acetone balance','dn_acetone/dt = reaction_rate - total_liquid_outflow * x_acetone.'),
 ('Equation_CSTR_Energy','CSTR energy balance','dU/dt = F_in*h_in - F_liq*out*h_out - F_H2*h_H2 + Q_jacket - Q_loss - Q_emergency - rate*ΔH_rxn.'),
 ('Equation_CSTR_H2','CSTR hydrogen balance','dn_H2/dt = reaction_rate - Fvent.'),
 ('Equation_CSTR_Pressure','CSTR H2 pressure','P = n_H2 * R * T / V_gas(M_liq), using reactor headspace plus 22.75 m3 connected H2-system capacitance.'),
])
source(po_cstr,'integrated_plant/cstr.py','CSTRDAE.residual')
po_h2=procop('PO_H2_Removal','PO – Hydrogen PCV/BDV/PSV Removal','module_H2Protection',[U('product_H2'),info_uri['cstr_vent_valve'],info_uri['cstr_bdv_valve'],info_uri['cstr_pressure_bar']],[U('product_h2_to_flare'),info_uri['cstr_h2_vent_kmol_h']],'Total H2 removal is the sum of normal PCV, emergency BDV, and passive PSV flows.',[
 ('Equation_H2_Removal','Hydrogen removal flow','Fvent = PCV_open*Cv_pcv*sqrt(max(P-Pback,0)) + BDV_open*Cv_bdv*sqrt(max(P-Pback,0)) + PSV_open(P)*Cv_psv*sqrt(max(P-Pback,0)); PSV starts at 4.50 bar and is full-open by 4.70 bar.')])
source(po_h2,'integrated_plant/cstr.py','CSTRDAE.h2_flow_components')
po_col=procop('PO_Column_Stages','PO – 10-Stage Distillation Separation','module_Column',[U('product_cstr_liquid'),info_uri['cstr_outflow'],info_uri['reflux_flow']],[U('product_overhead_vapor'),U('product_bottoms')],'Ten equilibrium-stage dynamic column; feed enters tray 6 from the top.',[
 ('Equation_Tray_Total','Tray total balance','dM_i/dt = L_in + V_in + F_i - L_i - V_i.'),
 ('Equation_Tray_Component','Tray light-component balance','dn_i/dt = L_in*x_in + V_in*y_in + F_i*z_i - L_i*x_i - V_i*y_i.'),
 ('Equation_Tray_Energy','Tray energy balance','dU_i/dt = L_in*hL_in + V_in*hV_in + F_i*h_feed - L_i*hL_i - V_i*hV_i.'),
 ('Equation_Tray_Hydraulics','Tray hydraulics and vapour flow','L_i = L_nominal*(M_i/M_nominal)^1.5; V_i = Cv_vap*sqrt(max(P_i-P_above,0)); VLE uses bubble pressure and y_light relations.'),
])
source(po_col,'integrated_plant/distillation.py','DistillationDAE.residual')
po_reb=procop('PO_Reboiler','PO – Reboiler','module_Reboiler',[U('product_bottoms'),info_uri['steam_factor'],info_uri['steam_pressure_bar'],info_uri['bottoms_flow'],info_uri['reboiler_liquid_leak_kmol_h']],[U('product_bottoms'),info_uri['reboiler_holdup_kmol'],info_uri['bottom_temperature_K'],info_uri['reboiler_pressure_bar']],'Steam-heated dynamic reboiler returns vapour to tray 10 and withdraws bottoms; physical leak removes mass/component/enthalpy.',[
 ('Equation_Reboiler','Reboiler balances','Q_reb = UA_reboiler*steam_factor*positive(T_steam - T_reboiler); total, light-component and energy balances include bottoms + leak withdrawal and vapour return.')])
source(po_reb,'integrated_plant/distillation.py','DistillationDAE.residual')
po_cond=procop('PO_Condenser','PO – Condenser Capacity and Column Pressure','module_CondenserDrum',[U('product_overhead_vapor'),info_uri['condenser_cooling_valve'],info_uri['condenser_cooling_effectiveness'],info_uri['cooling_water_temperature_K']],[U('product_condensate'),info_uri['column_pressure_bar']],'Condenser capacity is limited by available cooling duty; column pressure integrates the imbalance between top vapour generation and condensation.',[
 ('Equation_CondenserCapacity','Condenser capacity','Qavail = UA_condenser*cooling_valve*effectiveness*positive(T_top-T_CW); capacity = Qavail/condenser_capacity_per_kmol; Fcond = min(capacity,demand).'),
 ('Equation_ColumnPressure','Lumped column pressure dynamics','dPcond/dt = (V_top - Fcond) / vapour_capacitance.')])
source(po_cond,'integrated_plant/distillation.py','DistillationDAE.condenser_capacity')
po_drum=procop('PO_RefluxDrum','PO – Reflux Drum','module_CondenserDrum',[U('product_condensate'),info_uri['reflux_flow'],info_uri['distillate_flow'],info_uri['drum_liquid_leak_kmol_h']],[U('product_distillate'),info_uri['drum_holdup_kmol'],info_uri['distillate_x_acetone']],'Reflux drum accumulates condensed overhead and splits withdrawal into reflux, distillate and physical leak.',[
 ('Equation_RefluxDrum','Reflux drum balances','dM_drum/dt = Fcond - (reflux + distillate + drum_leak); light-component and energy balances use the same total withdrawal.')])
source(po_drum,'integrated_plant/distillation.py','DistillationDAE.residual')

# process successor graph
link(po_feed,VDI3682.hasSuccessor,po_cstr); link(po_cstr,VDI3682.hasSuccessor,po_h2,po_col); link(po_col,VDI3682.hasSuccessor,po_reb,po_cond); link(po_cond,VDI3682.hasSuccessor,po_drum); link(po_drum,VDI3682.hasSuccessor,po_col)

# ---------- command arbitration chain ----------
stages=[
 ('stage_ControllerRequest','1. BPCS / Controller Request','ctrl.* values generated by regulatory control.'),
 ('stage_SupervisoryRequest','2. Supervisory / Operator Request','sup.* values after persistent operator/agent actions.'),
 ('stage_SafetyCommand','3. Safety-Arbitrated Command','safe.* values after independent SIF overrides.'),
 ('stage_ActualInput','4. Actual Plant Input','act.* values after actuator and selected-pump faults.'),
]
for sid,label,comment in stages:
    s=add(U(sid),INT.CommandStage,label,comment); link(system,INT.hasCommandStage,s)
for a,b in zip(stages,stages[1:]): link(U(a[0]),INT.stageSuccessor,U(b[0]))

# ---------- safety interlocks ----------
sifs={
 'R_T_HH':('cstr_temperature_K','HIGH',348.0,False,[('cstr_heater_factor','set',0.0),('cstr_emergency_cooling_factor','max',1.0)]),
 'R_P_HH':('cstr_pressure_bar','HIGH',4.00,False,[('cstr_heater_factor','set',0.0),('cstr_vent_valve','set',1.0),('cstr_bdv_valve','set',1.0)]),
 'R_P_HHH':('cstr_pressure_bar','HIGH',4.35,True,[('fresh_ipa_feed','min_fraction_of_nominal',0.40),('cstr_heater_factor','set',0.0),('cstr_vent_valve','set',1.0),('cstr_bdv_valve','set',1.0)]),
 'R_L_HH':('cstr_holdup_kmol','HIGH',72.0,False,[('fresh_ipa_feed','set',0.0),('cstr_heater_factor','set',0.0)]),
 'R_L_LL':('cstr_holdup_kmol','LOW',20.0,False,[('cstr_heater_factor','set',0.0),('cstr_outflow','set',0.0)]),
 'RB_L_LL':('reboiler_holdup_kmol','LOW',12.08,False,[('steam_factor','set',0.0),('bottoms_flow','set',0.0)]),
 'RB_L_HH':('reboiler_holdup_kmol','HIGH',51.76,True,[('steam_factor','set',0.0),('cstr_outflow','set',0.0),('fresh_ipa_feed','set',0.0),('cstr_heater_factor','set',0.0)]),
 'DR_L_LL':('drum_holdup_kmol','LOW',10.59,True,[('reflux_flow','set',0.0),('distillate_flow','set',0.0),('steam_factor','set',0.0),('cstr_outflow','set',0.0),('fresh_ipa_feed','set',0.0),('cstr_heater_factor','set',0.0)]),
 'DR_L_HH':('drum_holdup_kmol','HIGH',45.37,True,[('steam_factor','set',0.0),('cstr_outflow','set',0.0),('fresh_ipa_feed','set',0.0),('cstr_heater_factor','set',0.0)]),
 'COL_P_HH':('column_pressure_bar','HIGH',1.35,True,[('steam_factor','set',0.0),('condenser_cooling_valve','max',1.0),('cstr_outflow','set',0.0),('fresh_ipa_feed','set',0.0),('cstr_heater_factor','set',0.0)]),
}
safety_sys=component('SIS-001','Independent Safety System','module_Safety',(VDI2206.InformationProcessing,), 'Deterministic, independent, latched SIF layer. Final-element faults are applied after this safety layer.')
for sid,(tag,direction,limit,ptrip,overrides) in sifs.items():
    sif=add(U('SIF_'+sid),INT.SafetyInterlock,'SIF '+sid,f'Independent latched safety function monitoring {tag}; benchmark threshold, not an engineered SIL set point.')
    link(system,INT.hasInterlock,sif); link(sif,INT.monitorsParameter,param_uri[tag]); link(sif,INT.implementedBy,safety_sys); lit(sif,INT.direction,direction); lit(sif,INT.limitValue,float(limit),XSD.double); lit(sif,INT.plantTrip,bool(ptrip),XSD.boolean); source(sif,'integrated_plant/safety.py','SafetySystem.apply')
    for j,(target,op,val) in enumerate(overrides,1):
        rule=add(U(f'override_{sid}_{j}'),INT.OverrideRule,f'{sid} override {j}',f'{op} {target} to/at {val}.'); link(sif,INT.hasOverrideRule,rule); link(rule,INT.overrideTarget,param_uri[target]); lit(rule,INT.operator,op); lit(rule,INT.commandValue,float(val),XSD.double)

# ---------- alarms ----------
alarm_rows=[
 ('R_P_H','cstr_pressure_bar','Reactor pressure high','HIGH',3.50,2,0.05,'bar'),('R_P_HH','cstr_pressure_bar','Reactor pressure high-high','HIGH',3.80,1,0.05,'bar'),
 ('R_T_H','cstr_temperature_K','Reactor temperature high','HIGH',342.0,2,0.5,'K'),('R_T_HH','cstr_temperature_K','Reactor temperature high-high','HIGH',346.0,1,0.5,'K'),
 ('R_L_H','cstr_holdup_kmol','Reactor level high','HIGH',62.0,2,1.0,'kmol'),('R_L_HH','cstr_holdup_kmol','Reactor level high-high','HIGH',68.0,1,1.0,'kmol'),('R_L_L','cstr_holdup_kmol','Reactor level low','LOW',38.0,2,1.0,'kmol'),('R_L_LL','cstr_holdup_kmol','Reactor level low-low','LOW',25.0,1,1.0,'kmol'),
 ('RB_L_HH','reboiler_holdup_kmol','Reboiler level high-high','HIGH',48.0,1,1.0,'kmol'),('RB_L_LL','reboiler_holdup_kmol','Reboiler level low-low','LOW',16.0,1,1.0,'kmol'),
 ('DR_L_HH','drum_holdup_kmol','Reflux drum level high-high','HIGH',42.0,1,1.0,'kmol'),('DR_L_LL','drum_holdup_kmol','Reflux drum level low-low','LOW',14.0,1,1.0,'kmol'),
 ('COL_P_H','column_pressure_bar','Column pressure high','HIGH',1.15,2,0.02,'bar'),('COL_P_HH','column_pressure_bar','Column pressure high-high','HIGH',1.28,1,0.02,'bar'),
 ('DIST_X_L','distillate_x_acetone','Distillate acetone purity low','LOW',0.90,3,0.01,'mol frac'),('BTM_X_H','bottoms_x_acetone','Bottoms acetone high','HIGH',0.08,3,0.01,'mol frac'),
]
# pump status observable params
for tid in pump_defs:
    tag=f'pump_{tid}_failed'; p=add(U('obs_param_'+tag),(DIN17359.Parameter,SOSA.ObservableProperty),'obs_param_'+tag,f'Boolean status: selected pump in {tid} train is failed.'); lit(p,INT.unit,'bool'); param_uri[tag]=p
for row in [('P101_TRIP','pump_FEED_failed','Fresh-feed duty pump trip / no selected pump flow','HIGH',0.5,1,0.0,'bool'),('P102_TRIP','pump_CSTR_OUTLET_failed','CSTR outlet / column-feed duty pump trip','HIGH',0.5,1,0.0,'bool'),('P201_TRIP','pump_REFLUX_failed','Reflux duty pump trip','HIGH',0.5,1,0.0,'bool'),('P202_TRIP','pump_DISTILLATE_failed','Distillate duty pump trip','HIGH',0.5,1,0.0,'bool'),('P203_TRIP','pump_BOTTOMS_failed','Bottoms duty pump trip','HIGH',0.5,1,0.0,'bool')]: alarm_rows.append(row)
for aid,tag,desc,direction,limit,prio,hyst,unit in alarm_rows:
    a=add(U('alarm_'+aid),INT.Alarm,aid+' – '+desc,'Operator alarm; logic uses BPCS measurements and is separate from SIF latching/acknowledgement.')
    link(system,INT.hasAlarm,a); link(a,INT.monitorsParameter,param_uri[tag]); lit(a,INT.direction,direction); lit(a,INT.limitValue,float(limit),XSD.double); lit(a,INT.priority,int(prio),XSD.integer); lit(a,INT.hysteresis,float(hyst),XSD.double); lit(a,INT.unit,unit); source(a,'integrated_plant/alarms.py','default_alarm_definitions')
    if aid in sifs: link(a,INT.relatedSIF,U('SIF_'+aid))

# ---------- disturbances ----------
disturbances={
 'dist_feed_temperature':('Feed Temperature Disturbance','fresh_feed_temperature_K'),
 'dist_cooling_water_temperature':('Cooling-Water Temperature Disturbance','cooling_water_temperature_K'),
 'dist_steam_header_pressure':('Steam-Header Pressure Disturbance','steam_pressure_bar'),
 'dist_heating_utility_temperature':('CSTR Heating-Utility Temperature Disturbance','cstr_heating_utility_temperature_K'),
 'dist_ambient_temperature':('Ambient Temperature Disturbance','ambient_temperature_K'),
}
for did,(label,target) in disturbances.items():
    d=add(U(did),INT.Disturbance,label,'Normal-variability generator may apply one 15 min rectangular ±10% step to this variable; different disturbances may overlap.')
    link(d,INT.affectsParameter,param_uri[target]); lit(d,INT.mode,'15_min_rectangular_step_±10_percent'); source(d,'integrated_plant/scenario.py','randomized_step_disturbance_scenario')

# ---------- features/symptoms/faults ----------
features={
 'feat_reactor_pressure':('Reactor pressure deviation','cstr_pressure_bar'),
 'feat_reactor_temperature':('Reactor temperature deviation','cstr_temperature_K'),
 'feat_reactor_level':('Reactor holdup deviation','cstr_holdup_kmol'),
 'feat_reboiler_level':('Reboiler holdup deviation','reboiler_holdup_kmol'),
 'feat_drum_level':('Reflux drum holdup deviation','drum_holdup_kmol'),
 'feat_column_pressure':('Column pressure deviation','column_pressure_bar'),
 'feat_distillate_quality':('Distillate quality deviation','distillate_x_acetone'),
 'feat_bottoms_quality':('Bottoms quality deviation','bottoms_x_acetone'),
 'feat_conversion':('CSTR conversion / acetone fraction deviation','cstr_x_acetone'),
}
for fid,(label,tag) in features.items():
    f=add(U(fid),DIN17359.Feature,label,f'Feature derived from {tag}.'); link(param_uri[tag],DIN17359.ParameterIsDescribedByFeature,f)

symptoms={
 'sym_reactor_pressure_high':('Reactor Pressure High','feat_reactor_pressure'),
 'sym_reactor_temperature_high':('Reactor Temperature High','feat_reactor_temperature'),
 'sym_reactor_level_high':('Reactor Level High','feat_reactor_level'),
 'sym_reactor_level_low':('Reactor Level Low','feat_reactor_level'),
 'sym_reboiler_level_low':('Reboiler Level Low','feat_reboiler_level'),
 'sym_drum_level_low':('Reflux Drum Level Low','feat_drum_level'),
 'sym_column_pressure_high':('Column Pressure High','feat_column_pressure'),
 'sym_distillate_purity_low':('Distillate Acetone Purity Low','feat_distillate_quality'),
 'sym_bottoms_acetone_high':('Bottoms Acetone High','feat_bottoms_quality'),
 'sym_conversion_low':('CSTR Acetone Conversion Low','feat_conversion'),
 'sym_pcv_command_actual_mismatch':('PCV Command–Actual Mismatch',None),
 'sym_heater_command_actual_mismatch':('Heater Command–Actual Mismatch',None),
 'sym_outlet_flow_loss':('CSTR Outlet / Column Feed Flow Lost or Capacity-Limited',None),
 'sym_reflux_flow_loss':('Reflux Flow Lost or Stuck',None),
 'sym_condenser_valve_saturated_open':('Condenser Cooling Valve Saturated Open',None),
 'sym_pressure_measurement_low_bias':('Reactor Pressure Measurement Biased Low',None),
 'sym_top_temperature_frozen':('Top Temperature Measurement Frozen',None),
 'sym_mass_imbalance_cstr':('CSTR Mass Imbalance / Unaccounted Withdrawal',None),
 'sym_mass_imbalance_reboiler':('Reboiler Mass Imbalance / Unaccounted Withdrawal',None),
 'sym_mass_imbalance_drum':('Reflux Drum Mass Imbalance / Unaccounted Withdrawal',None),
}
for sid,(label,fid) in symptoms.items():
    s=add(U(sid),DIN17359.Symptom,label,label+'.')
    if fid: link(s,DIN17359.SymptomIsExtractedFromFeature,U(fid))

fault_defs={
 'fault_h2_pcv_capacity_degradation':('H2 PCV Capacity Degradation','actuator','PCV-101','cstr_vent_valve',['sym_pcv_command_actual_mismatch','sym_reactor_pressure_high']),
 'fault_h2_pcv_stuck_closed':('H2 PCV Stuck Closed','actuator','PCV-101','cstr_vent_valve',['sym_pcv_command_actual_mismatch','sym_reactor_pressure_high']),
 'fault_reactor_heater_stuck_high':('Reactor Heater Stuck High','actuator','E-101','cstr_heater_factor',['sym_heater_command_actual_mismatch','sym_reactor_temperature_high']),
 'fault_cstr_outlet_capacity_loss':('CSTR Outlet Capacity Loss','actuator','P-102A','cstr_outflow',['sym_outlet_flow_loss','sym_reactor_level_high']),
 'fault_reflux_pump_trip':('P-201A Reflux Pump Trip','pump','P-201A','reflux_flow',['sym_reflux_flow_loss','sym_distillate_purity_low']),
 'fault_reflux_drum_leak':('Reflux Drum Liquid Leak','process','V-201','drum_liquid_leak_kmol_h',['sym_drum_level_low','sym_mass_imbalance_drum']),
 'fault_reactor_pressure_sensor_low_bias':('Reactor Pressure Sensor Low Bias','sensor','PT-101','cstr_pressure_bar',['sym_pressure_measurement_low_bias','sym_reactor_pressure_high']),
 'fault_condenser_cooling_loss':('Condenser Cooling Effectiveness Loss','utility','E-201','condenser_cooling_effectiveness',['sym_condenser_valve_saturated_open','sym_column_pressure_high']),
 'fault_reflux_valve_stiction':('Reflux Flow Stiction','actuator','P-201A','reflux_flow',['sym_reflux_flow_loss','sym_distillate_purity_low']),
 'fault_top_temperature_sensor_freeze':('Top Temperature Sensor Freeze','sensor','TT-201','top_temperature_K',['sym_top_temperature_frozen']),
 'fault_catalyst_activity_loss':('Catalyst Activity / Reaction-Rate Loss','process','R-101','catalyst_activity',['sym_conversion_low']),
 'fault_cstr_liquid_leak':('CSTR Liquid Leak','process','R-101','cstr_liquid_leak_kmol_h',['sym_reactor_level_low','sym_mass_imbalance_cstr']),
 'fault_reboiler_liquid_leak':('Reboiler Liquid Leak','process','E-202','reboiler_liquid_leak_kmol_h',['sym_reboiler_level_low','sym_mass_imbalance_reboiler']),
 'fault_cstr_heater_output_freeze':('CSTR Heater Output Freeze','actuator','E-101','cstr_heater_factor',['sym_heater_command_actual_mismatch']),
 'fault_bottoms_flow_freeze':('Bottoms Flow Output Freeze','actuator','P-203A','bottoms_flow',['sym_reboiler_level_low']),
 'fault_reflux_flow_freeze':('Reflux Flow Output Freeze','actuator','P-201A','reflux_flow',['sym_reflux_flow_loss']),
}
for fid,(label,kind,subject,target,syms) in fault_defs.items():
    f=add(U(fid),(DIN17359.Fault,INT.FaultMode),label,f'Fault mode implemented in the simulator/fault library; category={kind}, target={target}.')
    lit(f,INT.faultKind,kind); link(f,INT.affectsParameter,param_uri[target]); source(f,'integrated_plant/prototype_scenarios.py' if 'freeze' not in fid or fid=='fault_top_temperature_sensor_freeze' else 'integrated_plant/scenario.py')
    link(U(subject),DIN17359.DiagnosticSubjectHasFault,f)
    for sid in syms: link(f,DIN17359.FaultIsDescribedBySymptom,U(sid)); link(f,INT.causesSymptom,U(sid)); link(U(sid),DIN17359.SymptomDescribesFault,f)

# ---------- operator authority ----------
auth=add(U('OperatorAuthority'),INT.OperatorAuthorityModel,'Restricted Operator / Agent Authority','Normal supervisory authority explicitly excludes fault/environment injection and SIF threshold/bypass modification.')
source(auth,'integrated_plant/episode.py','EpisodeRunner.action_catalog')
actions={
 'action_NO_ACTION':('NO_ACTION','No supervisory change.'),
 'action_SET_THROUGHPUT':('SET_THROUGHPUT','Set persistent plant throughput target, allowed range 0–150 kmol/h.'),
 'action_SET_SETPOINT':('SET_SETPOINT','Change any named regulatory loop setpoint within its LoopAccessSpec bounds.'),
 'action_SET_MODE':('SET_MODE','Transfer any named regulatory loop between AUTO and MANUAL.'),
 'action_SET_MANUAL_OUTPUT':('SET_MANUAL_OUTPUT','Set loop manual output after the loop is in MANUAL, within controller MV bounds.'),
 'action_OPERATE_AUXILIARY':('OPERATE_AUXILIARY','Command H2_BDV or EMERGENCY_COOLING, each in [0,1].'),
 'action_START_SPARE':('START_SPARE','Start/select the healthy standby pump in a named pump train.'),
 'action_SELECT_PRIMARY':('SELECT_PRIMARY','Return to a healthy primary pump in a named pump train.'),
 'action_ACK_ALARM':('ACK_ALARM','Acknowledge one or all operator alarms; acknowledgement does not reset SIF latches.'),
 'action_RESET_SIF':('RESET_SIF','Request SIF reset; only latches whose process variables are inside reset hysteresis are cleared.'),
}
for aid,(label,comment) in actions.items():
    a=add(U(aid),INT.OperatorAction,label,comment); link(auth,INT.allowsAction,a)
link(U('action_SET_THROUGHPUT'),INT.actsOn,param_uri['fresh_ipa_feed']); lit(U('action_SET_THROUGHPUT'),INT.allowedMinimum,0.0,XSD.double); lit(U('action_SET_THROUGHPUT'),INT.allowedMaximum,150.0,XSD.double); lit(U('action_SET_THROUGHPUT'),INT.unit,'kmol/h')
for loop,ctrl in controller_uri.items():
    link(U('action_SET_SETPOINT'),INT.actsOn,ctrl); link(U('action_SET_MODE'),INT.actsOn,ctrl); link(U('action_SET_MANUAL_OUTPUT'),INT.actsOn,ctrl)
for tag in ['cstr_bdv_valve','cstr_emergency_cooling_factor']: link(U('action_OPERATE_AUXILIARY'),INT.actsOn,param_uri[tag])
for tid in pump_defs: link(U('action_START_SPARE'),INT.actsOn,U('pump_train_'+tid)); link(U('action_SELECT_PRIMARY'),INT.actsOn,U('pump_train_'+tid))
for a in alarm_rows: link(U('action_ACK_ALARM'),INT.actsOn,U('alarm_'+a[0]))
for sid in sifs: link(U('action_RESET_SIF'),INT.actsOn,U('SIF_'+sid))
for txt in ['fault activation','sensor bias/freeze','catalyst activity','leak rates','utility temperatures/pressures','condenser cooling effectiveness','SIF thresholds/bypass']:
    lit(auth,INT.prohibitsProperty,txt)

# ---------- episode outcome state machine ----------
sm=add(U('EpisodeOutcome_StateMachine'),UML.StateMachine,'Episode Outcome State Machine','State machine derived from OutcomeEvaluator and EpisodeStatus.')
source(sm,'integrated_plant/outcome.py','EpisodeStatus / OutcomeEvaluator.update')
state_comments={
 'RUNNING':'Episode is active and process simulation continues.',
 'COMPLETED':'Horizon reached when horizon_is_completion is true.',
 'RECOVERED':'After abnormal operation, external abnormalities and SIF latches are cleared and the plant remains near nominal for the configured dwell.',
 'SAFE_TRIP':'Plant-trip SIF is latched and stable in the broad safe envelope, or terminate_on_plant_trip is enabled.',
 'DAMAGE':'Damage exposure or CSTR overflow criterion is reached.',
 'MODEL_LIMIT':'A tray holdup falls below the model-validity floor.',
 'TIMEOUT':'Episode horizon reached without a completion-designated run.',
 'NUMERICAL_FAILURE':'Numerical solver failure terminates the episode.',
}
for st,comment in state_comments.items():
    s=add(U('state_'+st),(UML.State,INT.EpisodeOutcome),st+' State',comment); link(sm,UML.contains,s); link(system,INT.hasOutcomeState,s)
transitions={
 'transition_running_completed':('COMPLETED','t >= horizon_h AND horizon_is_completion == True'),
 'transition_running_recovered':('RECOVERED','abnormal_seen AND NOT external_abnormal_active AND no SIF latched AND near_nominal for 15 min'),
 'transition_running_safe_trip':('SAFE_TRIP','(terminate_on_plant_trip AND plant_trip_latched) OR (plant_trip_latched AND safe_envelope for 15 min)'),
 'transition_running_damage':('DAMAGE','reactor P >= 5.0 bar for 2 min OR column P >= 1.8 bar for 2 min OR reboiler holdup <= 5 kmol with steam > 0.05 for 5 min OR CSTR holdup >= 79 kmol'),
 'transition_running_model_limit':('MODEL_LIMIT','any tray holdup < 0.10 kmol'),
 'transition_running_timeout':('TIMEOUT','t >= horizon_h AND horizon_is_completion == False'),
 'transition_running_numerical_failure':('NUMERICAL_FAILURE','implicit integration / nonlinear solve fails'),
}
for tid,(target,guard) in transitions.items():
    t=add(U(tid),UML.Transition,'RUNNING → '+target,f'Outcome transition to {target}.'); link(t,UML.source,U('state_RUNNING')); link(t,UML.target,U('state_'+target)); lit(t,UML.transitionGuard,guard); lit(t,INT.guardExpression,guard)

# ---------- System model ----------
smodel=add(U('IntegratedPlant_SystemModel'),CPSMod.SystemModel,'Integrated CSTR–Distillation Digital Twin System Model','System model corresponding to the v0.5.3 direct-residual DAE plus control, safety, fault, measurement and operator layers.')
link(smodel,CPSMod.consistsOfBehaviorModel,U('Equation_CSTR_Rate'),U('Equation_CSTR_Mass'),U('Equation_CSTR_Energy'),U('Equation_Tray_Total'),U('Equation_Tray_Component'),U('Equation_Reboiler'),U('Equation_CondenserCapacity'),U('Equation_ColumnPressure'))
link(smodel,CPSMod.consistsOfStructureModel,system); link(smodel,CPSMod.consistsOfFunctionModel,process)

# ---------- serialization ----------
json_path=OUT/'Integrated_CSTR_Distillation_Knowledge_Graph.jsonld'
trig_path=OUT/'Integrated_CSTR_Distillation_Knowledge_Graph.trig'
# rdflib JSON-LD serializer will preserve the named graph and emit full URIs when auto_compact is false.
ds.serialize(destination=str(json_path), format='json-ld', indent=2, auto_compact=False)
ds.serialize(destination=str(trig_path), format='trig')

# Query pack
sparql = f'''# ============================================================================\n# INTEGRATED CSTR + DISTILLATION KG — GRAPHDB SPARQL QUERY PACK\n# Named graph: <{GRAPH_URI}>\n# ============================================================================\n\nPREFIX : <{BASE}>\nPREFIX ip: <{INT}>\nPREFIX VDI3682: <{VDI3682}>\nPREFIX VDI2206: <{VDI2206}>\nPREFIX CPSMod: <{CPSMod}>\nPREFIX DIN17359: <{DIN17359}>\nPREFIX sosa: <{SOSA}>\nPREFIX UML: <{UML}>\nPREFIX rdf: <{RDF}>\nPREFIX rdfs: <{RDFS}>\n\n# QUERY 1 — PROCESS OVERVIEW FOR AN LLM / AGENT\nCONSTRUCT {{\n  ?process a VDI3682:Process ; rdfs:label ?processLabel .\n  ?process VDI3682:consistsOf ?po .\n  ?po a VDI3682:ProcessOperator ; rdfs:label ?poLabel .\n  ?po VDI3682:hasInput ?input ; VDI3682:hasOutput ?output .\n  ?po VDI3682:isAssignedTo ?module .\n  ?po CPSMod:processRealizesBehavior ?model .\n  ?model a VDI2206:MathematicalModel ; rdfs:label ?modelLabel ; rdfs:comment ?modelComment .\n  ?module a VDI2206:Module ; rdfs:label ?moduleLabel .\n  ?module VDI2206:ModuleConsistsOfComponent ?component .\n  ?component rdf:type ?componentType ; rdfs:label ?componentLabel .\n}}\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?process a VDI3682:Process ; rdfs:label ?processLabel .\n  OPTIONAL {{ ?process VDI3682:consistsOf ?po . ?po a VDI3682:ProcessOperator . OPTIONAL {{?po rdfs:label ?poLabel}} OPTIONAL {{?po VDI3682:hasInput ?input}} OPTIONAL {{?po VDI3682:hasOutput ?output}} OPTIONAL {{?po VDI3682:isAssignedTo ?module}} OPTIONAL {{?po CPSMod:processRealizesBehavior ?model . OPTIONAL {{?model rdfs:label ?modelLabel}} OPTIONAL {{?model rdfs:comment ?modelComment}} }} }}\n  OPTIONAL {{ ?module a VDI2206:Module . OPTIONAL {{?module rdfs:label ?moduleLabel}} OPTIONAL {{?module VDI2206:ModuleConsistsOfComponent ?component . OPTIONAL {{?component rdf:type ?componentType}} OPTIONAL {{?component rdfs:label ?componentLabel}} }} }}\n}}\n\n# QUERY 2 — REGULATORY CONTROL LOOPS, PV/MV, SETPOINTS AND BOUNDS\nSELECT ?loop ?label ?pv ?pvLabel ?mv ?mvLabel ?sp ?spValue ?spMin ?spMax ?mvMin ?mvMax\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?loop a VDI2206:InformationProcessing ; rdfs:label ?label ;\n        ip:monitorsParameter ?pv ; ip:controlsParameter ?mv ; ip:hasSetpoint ?sp .\n  ?pv rdfs:label ?pvLabel . ?mv rdfs:label ?mvLabel .\n  ?sp DIN17359:referenceValue ?spValue .\n  OPTIONAL {{?loop ip:setpointMinimum ?spMin}} OPTIONAL {{?loop ip:setpointMaximum ?spMax}}\n  OPTIONAL {{?loop ip:mvMinimum ?mvMin}} OPTIONAL {{?loop ip:mvMaximum ?mvMax}}\n}} ORDER BY ?loop\n\n# QUERY 3 — SAFETY INTERLOCKS AND EXACT OVERRIDES\nCONSTRUCT {{\n  ?sif a ip:SafetyInterlock ; rdfs:label ?label ; ip:monitorsParameter ?pv ;\n       ip:direction ?direction ; ip:limitValue ?limit ; ip:plantTrip ?plantTrip ; ip:hasOverrideRule ?rule .\n  ?rule ip:overrideTarget ?target ; ip:operator ?op ; ip:commandValue ?value .\n}}\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?sif a ip:SafetyInterlock ; rdfs:label ?label ; ip:monitorsParameter ?pv ;\n       ip:direction ?direction ; ip:limitValue ?limit ; ip:plantTrip ?plantTrip .\n  OPTIONAL {{ ?sif ip:hasOverrideRule ?rule . ?rule ip:overrideTarget ?target ; ip:operator ?op ; ip:commandValue ?value . }}\n}}\n\n# QUERY 4 — FAULT → SUBJECT → AFFECTED PARAMETER → SYMPTOM\nSELECT ?fault ?faultLabel ?kind ?subject ?subjectLabel ?parameter ?parameterLabel ?symptom ?symptomLabel\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?fault a DIN17359:Fault ; rdfs:label ?faultLabel .\n  OPTIONAL {{?fault ip:faultKind ?kind}} OPTIONAL {{?fault ip:affectsParameter ?parameter . ?parameter rdfs:label ?parameterLabel}}\n  OPTIONAL {{?subject DIN17359:DiagnosticSubjectHasFault ?fault . ?subject rdfs:label ?subjectLabel}}\n  OPTIONAL {{?fault DIN17359:FaultIsDescribedBySymptom ?symptom . ?symptom rdfs:label ?symptomLabel}}\n}} ORDER BY ?fault ?symptom\n\n# QUERY 5 — OPERATOR / AGENT AUTHORITY\nCONSTRUCT {{\n  :OperatorAuthority a ip:OperatorAuthorityModel ; ip:allowsAction ?action ; ip:prohibitsProperty ?prohibited .\n  ?action a ip:OperatorAction ; rdfs:label ?actionLabel ; rdfs:comment ?actionComment ; ip:actsOn ?target .\n}}\nFROM <{GRAPH_URI}>\nWHERE {{\n  :OperatorAuthority a ip:OperatorAuthorityModel .\n  OPTIONAL {{:OperatorAuthority ip:allowsAction ?action . OPTIONAL {{?action rdfs:label ?actionLabel}} OPTIONAL {{?action rdfs:comment ?actionComment}} OPTIONAL {{?action ip:actsOn ?target}} }}\n  OPTIONAL {{:OperatorAuthority ip:prohibitsProperty ?prohibited}}\n}}\n\n# QUERY 6 — COMMAND ARBITRATION CHAIN (CTRL → SUP → SAFE → ACTUAL)\nSELECT ?stage ?label ?next ?nextLabel\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?stage a ip:CommandStage ; rdfs:label ?label .\n  OPTIONAL {{?stage ip:stageSuccessor ?next . ?next rdfs:label ?nextLabel}}\n}} ORDER BY ?label\n\n# QUERY 7 — ALARMS WITH RELATED SIF THRESHOLDS\nSELECT ?alarm ?alarmLabel ?tag ?direction ?alarmLimit ?priority ?sif ?sifLimit\nFROM <{GRAPH_URI}>\nWHERE {{\n  ?alarm a ip:Alarm ; rdfs:label ?alarmLabel ; ip:monitorsParameter ?tag ; ip:direction ?direction ; ip:limitValue ?alarmLimit ; ip:priority ?priority .\n  OPTIONAL {{?alarm ip:relatedSIF ?sif . ?sif ip:limitValue ?sifLimit}}\n}} ORDER BY ?priority ?alarm\n\n# QUERY 8 — EPISODE OUTCOME STATE MACHINE\nCONSTRUCT {{\n  :EpisodeOutcome_StateMachine a UML:StateMachine ; UML:contains ?state .\n  ?state a UML:State ; rdfs:label ?stateLabel ; rdfs:comment ?stateComment .\n  ?transition a UML:Transition ; UML:source ?source ; UML:target ?target ; UML:transitionGuard ?guard .\n}}\nFROM <{GRAPH_URI}>\nWHERE {{\n  :EpisodeOutcome_StateMachine a UML:StateMachine .\n  {{ :EpisodeOutcome_StateMachine UML:contains ?state . OPTIONAL {{?state rdfs:label ?stateLabel}} OPTIONAL {{?state rdfs:comment ?stateComment}} }}\n  UNION\n  {{ ?transition a UML:Transition ; UML:source ?source ; UML:target ?target . OPTIONAL {{?transition UML:transitionGuard ?guard}} }}\n}}\n'''
sp_path=OUT/'Integrated_CSTR_Distillation_GraphDB_Queries.sparql'
sp_path.write_text(sparql,encoding='utf-8')

readme=f'''# Integrated CSTR + Distillation Knowledge Graph (GraphDB)\n\nGenerated from the uploaded v0.5.3 simulator and patterned after `CSTR_Knowledge_Graph.jsonld`.\n\n## Files\n- `Integrated_CSTR_Distillation_Knowledge_Graph.jsonld` — JSON-LD with a named graph.\n- `Integrated_CSTR_Distillation_Knowledge_Graph.trig` — equivalent TriG serialization; often the clearest GraphDB import format for a named graph.\n- `Integrated_CSTR_Distillation_GraphDB_Queries.sparql` — process, control, safety, fault, operator-authority, alarm and state-machine queries.\n\n## Named graph\n`<{GRAPH_URI}>`\n\n## Base namespace\n`<{BASE}>`\n\n## GraphDB import\nCreate/select a repository, open **Import → RDF**, upload either the `.jsonld` or `.trig` file, and import. The graph context is already encoded in the file; you do not need to assign another context.\n\n## Scope encoded\nThe graph includes the CSTR and H2 protection, 10 column trays, condenser/reflux drum/reboiler, five A/B duty-standby pump trains, 34+ observable process tags, manipulated/exogenous variables, eight PI loops, 21 alarms, ten SIF causes and their override rules, operator/agent actions and prohibited master variables, curated/surprise fault modes, disturbances, mathematical-model nodes, and the episode outcome state machine.\n\n## Important modelling choice\nThe simulator explicitly separates controller request → supervisory request → safety-commanded request → actual plant input after actuator/pump faults. The KG preserves this as four `ip:CommandStage` nodes. This is important because an SIF can command a safe position while a failed final element still does not move.\n'''
(OUT/'Integrated_CSTR_Distillation_KG_README.md').write_text(readme,encoding='utf-8')

print('Triples in named graph:', len(g))
print('Files:', json_path, trig_path, sp_path)
