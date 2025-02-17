
from requests import post
from fe_csdl_opt.fea.fea_dolfinx import *
from fe_csdl_opt.csdl_opt.fea_model import FEAModel
from fe_csdl_opt.csdl_opt.state_model import StateModel
from fe_csdl_opt.csdl_opt.output_model import OutputModel
import numpy as np
import csdl

from csdl_om import Simulator as om_simulator
from python_csdl_backend import Simulator as py_simulator
from matplotlib import pyplot as plt
import argparse

import motor_pde as pde
from postprocessor.power_loss_model import LossSumModel, PowerLossModel
from preprocessor.ffd_model import FFDModel, MotorMesh, MagnetShapeLimitModel
from preprocessor.boundary_input_model import BoundaryInputModel

###########################################################
#################### Preprocessing ########################
shift           = 2.5
mech_angles     = np.arange(0,30,5)
# rotor_rotations = np.pi/180*np.arange(0,30,5)
rotor_rotations = np.pi/180*mech_angles[:1]
instances       = len(rotor_rotations)

coarse_test = True

mm = MotorMesh(
    file_name='motor_data/motor_data_test/motor_mesh_test_1',
    popup=False,
    rotation_angles=rotor_rotations,
    base_angle=shift * np.pi/180,
    test=True
)

mm.baseline_geometry=True
mm.magnet_shift_only = True
mm.create_motor_mesh()
 # dictionary holding parametrization parameters
parametrization_dict = mm.ffd_param_dict
unique_sp_list = sorted(set(parametrization_dict['shape_parameter_list_input']))

# FFD MODEL
ffd_connection_model = FFDModel(
    parametrization_dict=parametrization_dict
)


'''
1. Define the mesh
'''
# TODO: write the msh2xdmf convertor in DOLFINx
mesh_name = "motor_mesh_test_1"
data_path = "motor_data/motor_data_medium/"
# data_path = "motor_data_latest_coarse/"

mesh_file = data_path + mesh_name
mesh, boundaries_mf, subdomains_mf, association_table = import_mesh(
    prefix=mesh_file,
    dim=2,
    subdomains=True
)

'''
The boundary movement data
'''
f = open(data_path+'init_edge_coords_coarse_1.txt', 'r+')
init_edge_coords = np.fromstring(f.read(), dtype=float, sep=' ')
f.close()

# f = open(data_path+'edge_coord_deltas_coarse_1.txt', 'r+')
# edge_deltas_from_file = np.fromstring(f.read(), dtype=float, sep=' ')
# f.close()


dx = Measure('dx', domain=mesh, subdomain_data=subdomains_mf)
dS = Measure('dS', domain=mesh, subdomain_data=boundaries_mf)
ds = Measure('ds', domain=mesh, subdomain_data=boundaries_mf)
# mesh = create_unit_square(MPI.COMM_WORLD, 12, 15)
winding_id = [15,]
magnet_id = [3,]
steel_id = [1,2]
winding_range = range(15,50+1)

# Subdomains for calculating power losses
ec_loss_subdomain = [1,2,] # rotor and stator core
hysteresis_loss_subdomain = [1,2,]
pm_loss_subdomain = range(3, 14+1)



###########################################################
######################### FEA #############################
'''
2. Set up the PDE problem
'''
# PROBLEM SPECIFIC PARAMETERS
Hc = 838.e3  # 838 kA/m
p = 12
s = 3 * p
vacuum_perm = 4e-7 * np.pi
angle = 0.
iq = 282.2  / 0.00016231
##################### mesh motion subproblem ######################
fea_mm = FEA(mesh)

fea_mm.PDE_SOLVER = 'SNES'
fea_mm.REPORT = True
fea_mm.record = False


# inputs for mesh motion subproblem
input_name_mm = 'uhat_bc'
input_function_space_mm = VectorFunctionSpace(mesh, ('CG', 1))
input_function_mm = Function(input_function_space_mm)
edge_indices = locateDOFs(init_edge_coords,input_function_space_mm)

boundary_input_model = BoundaryInputModel(edge_indices=edge_indices,
                                    output_size=len(input_function_mm.x.array))
############ User-defined incremental solver ###########
def getDisplacementSteps(uhat, edge_deltas):
    """
    Divide the edge movements into steps based on the current mesh size
    """

    mesh = uhat.function_space.mesh
    STEPS = 2
    max_disp = np.max(np.abs(edge_deltas))
    h = meshSize(mesh)
    move(mesh, uhat)
    min_cell_size = h.min()
    moveBackward(mesh, uhat)
    min_STEPS = 4*round(max_disp/min_cell_size)
    if min_STEPS >= STEPS:
        STEPS = min_STEPS
    increment_deltas = edge_deltas/STEPS
    return STEPS, increment_deltas

def advance(func_old,increment_deltas):
    func_old.vector[edge_indices.astype(np.int32)] += \
                    increment_deltas[edge_indices.astype(np.int32)]

def solveIncremental(res,func,bc,report=False):
    vec = np.copy(input_function_mm.vector.getArray())
    nnz_ind = np.nonzero(vec)[0]
    func_old = input_function_mm
    # Get the relative movements from the previous step
    relative_edge_deltas = func_old.vector[:] - func.vector[:]
    STEPS, increment_deltas = getDisplacementSteps(func_old,
                                                relative_edge_deltas)


    snes_solver = SNESSolver(res, func, bc, report=report)
    func_old.vector[:] = func.vector

    # Incrementally set the BCs to increase to `edge_deltas`
    if report == True:
        print(80*"=")
        print(' FEA: total steps for mesh motion:', STEPS)
        print(80*"=")
    for i in range(STEPS):
        if report == True:
            print(80*"=")
            print("  FEA: Step "+str(i+1)+"/"+str(STEPS)+" of mesh movement")
            print(80*"=")
        advance(func_old,increment_deltas)
        snes_solver.solve(None, func.vector)
    input_function_mm.vector.setArray(vec)
    if report == True:
        print(80*"=")
        print(' FEA: L2 error of the mesh motion on the edges:',
                np.linalg.norm(func.vector[nnz_ind.astype(np.int32)]
                         - input_function_mm.vector[nnz_ind.astype(np.int32)]))
        print(80*"=")

fea_mm.custom_solve = solveIncremental

# states for mesh motion subproblem
state_name_mm = 'uhat'
state_function_space_mm = VectorFunctionSpace(mesh, ('CG', 1))
state_function_mm = Function(state_function_space_mm)
state_function_mm.vector.set(0.0)
v_mm = TestFunction(state_function_space_mm)

# Add output to the PDE problem:
output_name_mm_1 = 'winding_area'
output_form_mm_1 = pde.area_form(state_function_mm, dx, winding_id)
output_name_mm_2 = 'magnet_area'
output_form_mm_2 = pde.area_form(state_function_mm, dx, magnet_id)
output_name_mm_3 = 'steel_area'
output_form_mm_3 = pde.area_form(state_function_mm, dx, steel_id)


############ Weakly enforced boundary conditions #############
residual_form_mm = pde.pdeResMM(state_function_mm, v_mm, g=input_function_mm,
                                nitsche=True, sym=True, overpenalty=False,
                                dS_=dS(1000),ds_=ds(1000))
fea_mm.add_input(name=input_name_mm,
                function=input_function_mm)
fea_mm.add_state(name=state_name_mm,
                function=state_function_mm,
                residual_form=residual_form_mm,
                arguments=[input_name_mm])


fea_mm.add_output(name=output_name_mm_1,
                type='scalar',
                form=output_form_mm_1,
                arguments=[state_name_mm])
fea_mm.add_output(name=output_name_mm_2,
                type='scalar',
                form=output_form_mm_2,
                arguments=[state_name_mm])
fea_mm.add_output(name=output_name_mm_3,
                type='scalar',
                form=output_form_mm_3,
                arguments=[state_name_mm])


#############################################################
################### electomagnetic subproblem ###############
fea_em = FEA(mesh)

fea_em.PDE_SOLVER = 'SNES'
fea_em.REPORT = True

# Add input to the PDE problem: the inputs as the previous states

# Add state to the PDE problem:
# states for electromagnetic equation: magnetic potential vector
state_name_em = 'A_z'
state_function_space_em = FunctionSpace(mesh, ('CG', 1))
state_function_em = Function(state_function_space_em)
v_em = TestFunction(state_function_space_em)



########################### Incremental solve ###########################
############### much slower, but more accurate ##########################
def solveIncrementalEM(res,func,bc,report=False):
    STEPS = 5
    # Incrementally set the BCs to increase to `edge_deltas`
    if report == True:
        print(80*"=")
        print(' FEA: total steps for electromagnetic solve:', STEPS)
        print(80*"=")
    JS_scaler = 1./STEPS
    res += pde.JS(v_em,state_function_mm,iq,p,s,Hc,angle)
    for i in range(STEPS):
        if report == True:
            print(80*"=")
            print("  FEA: Step "+str(i+1)+"/"+str(STEPS)+" of electromagnetic solve")
            print(80*"=")
        res -= JS_scaler*pde.JS(v_em,state_function_mm,iq,p,s,Hc,angle)
        # print(np.linalg.norm(getFuncArray(func)))
        snes_solver = SNESSolver(res, func, bc, report=report)
        snes_solver.solve(None, func.vector)

fea_em.custom_solve = solveIncrementalEM

#########################################################################

############ Strongly enforced boundary conditions #############
ubc_em = Function(state_function_space_em)
ubc_em.vector.set(0.0)
locate_BC1_em = locate_dofs_geometrical(
                    (state_function_space_em, state_function_space_em),
                    lambda x: np.isclose(x[0]**2+x[1]**2, 0.0144 ,atol=1e-6))
locate_BC2_em = locate_dofs_geometrical(
                    (state_function_space_em, state_function_space_em),
                    lambda x: np.isclose(x[0]**2+x[1]**2, 0.0036 ,atol=1e-6))

locate_BC_list_em = [locate_BC1_em, locate_BC2_em,]

# fea_em.add_strong_bc(ubc_em, locate_BC_list_em, state_function_space_em)
#
# residual_form_em = pde.pdeResEM(state_function_em,v_em,state_function_mm,
#                         iq,dx,p,s,Hc,vacuum_perm,angle)
#


############ Weakly enforced boundary conditions #############
residual_form_em = pde.pdeResEM(state_function_em,v_em,state_function_mm,
                        iq,dx,p,s,Hc,vacuum_perm,angle,
                        g=ubc_em,nitsche=True, sym=True, overpenalty=False,ds_=ds)

# Add output to the PDE problem:
output_name_1 = 'B_influence_eddy_current'
exponent_1 = 2
subdomains_1 = [1,2]
output_form_1 = pde.B_power_form(state_function_em, state_function_mm,
                            exponent_1, dx, subdomains_1)

output_name_2 = 'B_influence_hysteresis'
exponent_2 = 1.76835 # Material parameter for Hiperco 50
subdomains_2 = [1,2]
output_form_2 = pde.B_power_form(state_function_em, state_function_mm,
                            exponent_2, dx, subdomains_2)


'''
3. Define the boundary conditions
'''


fea_em.add_input(name=state_name_mm,
                function=state_function_mm)
fea_em.add_state(name=state_name_em,
                function=state_function_em,
                residual_form=residual_form_em,
                arguments=[state_name_mm])
fea_em.add_output(name=output_name_1,
                type='scalar',
                form=output_form_1,
                arguments=[state_name_em,state_name_mm])
fea_em.add_output(name=output_name_2,
                type='scalar',
                form=output_form_2,
                arguments=[state_name_em,state_name_mm])



'''
4. Set up the CSDL model
'''
fea_model = FEAModel(fea=[fea_mm,fea_em])
###########################################################
#################### Postprocessing #######################
# Case-to-case postprocessor model
model = csdl.Model()
power_loss_model = PowerLossModel()
loss_sum_model = LossSumModel()
magnet_shape_limit_model = MagnetShapeLimitModel()

###########################################################
######################## Connect ##########################

# python_csdl_backend
model.add(ffd_connection_model, name='ffd_model')
# model.add(magnet_shape_limit_model, name='magnet_shape_limit_model')
model.add(boundary_input_model, name='boundary_input_model')
model.add(fea_model, name='fea_model')
model.add(power_loss_model, name='power_loss_model')
model.add(loss_sum_model, name='loss_sum_model')


model.create_input('magnet_pos_delta_dv', val=0.1)
model.create_input('magnet_width_dv', val=0.)
model.create_input('motor_length', val=0.1)
model.create_input('frequency', val=300)
model.create_input('hysteresis_coeff', val=55.)
model.add_design_variable('magnet_pos_delta_dv', lower=-1e-5, upper=20.)
# model.add_design_variable('magnet_width_dv', lower=-15, upper=24.)
# model.add_constraint('magnet_shape_limit', upper=38.)
model.add_objective('loss_sum')

# from csdl import expand
# winding_area = model.declare_variable('winding_area')
# num_windings = 13
# nel = mesh.topology.index_map(mesh.topology.dim).size_local
# nn = mesh.topology.index_map(0).size_local
# i_amp_size = nel
# current_amplitude = model.create_input('current_amplitude', val=1.)
# current_density_amplitude = model.register_output(
#     'i_amp',
#     expand(current_amplitude / (winding_area/num_windings), (i_amp_size,))
# )

# current_density_amplitude = model.register_output(
    # 'i_amp', current_amplitude / (winding_area/num_windings))

sim = py_simulator(model, analytics=True)
# sim = om_simulator(model)
########### Test the forward solve ##############

####### Single steps of movement ##########

sim.run()
# sim.check_totals(of=['loss_sum','uhat','A_z'], wrt=['magnet_pos_delta_dv','magnet_width_dv'],compact_print=True)
# sim.executable.check_totals(of=['loss_sum','uhat','A_z'], wrt=['magnet_pos_delta_dv','magnet_width_dv'],compact_print=True)
# sim.executable.check_totals(of=['i_amp','winding_area'], wrt=['magnet_pos_delta_dv','magnet_width_dv'],compact_print=True)
# print("winding area:",sim['winding_area'])
# print("current density amplitude:",sim['i_amp'])
# ###### Multiple steps of movement ##########
# xdmf_uhat = XDMFFile(MPI.COMM_WORLD, "test4/record_uhat.xdmf", "w")
# xdmf_Az = XDMFFile(MPI.COMM_WORLD, "test4/record_Az.xdmf", "w")
# xdmf_B = XDMFFile(MPI.COMM_WORLD, "test4/record_B.xdmf", "w")
# xdmf_uhat.write_mesh(mesh)
# xdmf_Az.write_mesh(mesh)
# xdmf_B.write_mesh(mesh)
#
# delta = 20.
# N = 20
# ec_loss_array = np.zeros(N)
# hyst_loss_array = np.zeros(N)
# loss_sum_array = np.zeros(N)
# for i in range(N):
#     print(str(i)*40)
#     sim['magnet_pos_delta_dv'] = delta/N*i
#     print("magnet_pos_delta_dv:", sim['magnet_pos_delta_dv'])
#     # sim['magnet_width_dv'] = delta/N*i
#     # print("magnet_width_dv:", sim['magnet_width_dv'])
#
#     sim.run()
#     magnetic_flux_density = pde.B(state_function_em, state_function_mm)
#     print("Eddy current loss", sim['eddy_current_loss'])
#     print("Hysteresis loss", sim['hysteresis_loss'])
#     print("Loss sum", sim['loss_sum'])
#
#     ec_loss_array[i] = sim['eddy_current_loss']
#     hyst_loss_array[i] = sim['hysteresis_loss']
#     loss_sum_array[i] = sim['loss_sum']
#
#     # move(mesh, state_function_mm)
#     # xdmf_uhat.write_mesh(mesh)
#     xdmf_uhat.write_function(state_function_mm,i)
#     # xdmf_B.write_mesh(mesh)
#     xdmf_Az.write_function(state_function_em,i)
#     xdmf_B.write_function(magnetic_flux_density,i)
#     # moveBackward(mesh, state_function_mm)
#
#
# print("ec loss", ec_loss_array)
# print("hysteresis loss", hyst_loss_array)
# print("loss sum", loss_sum_array)

############# Run the optimization with modOpt #############
from modopt.csdl_library import CSDLProblem

prob = CSDLProblem(
    problem_name='em_motor_opt',
    simulator=sim,
)

from modopt.snopt_library import SNOPT

optimizer = SNOPT(prob,
                  Major_iterations = 100,
                  Major_optimality =1e-8,
                  Major_feasibility=1e-6,
                  append2file=True)
                  # append2file=False)


# Solve your optimization problem
# optimizer.solve()
print("="*40)

# fea_mm.inputs_dict[input_name_mm]['function'].vector.setArray(sim['uhat_bc'])
with XDMFFile(MPI.COMM_WORLD, "solutions/input_"+input_name_mm+".xdmf", "w") as xdmf:
    xdmf.write_mesh(fea_mm.mesh)
    fea_mm.inputs_dict[input_name_mm]['function'].name = input_name_mm
    xdmf.write_function(fea_mm.inputs_dict[input_name_mm]['function'])
with XDMFFile(MPI.COMM_WORLD, "solutions/state_"+state_name_mm+".xdmf", "w") as xdmf:
    xdmf.write_mesh(fea_mm.mesh)
    fea_mm.states_dict[state_name_mm]['function'].name = state_name_mm
    xdmf.write_function(fea_mm.states_dict[state_name_mm]['function'])

magnetic_flux_density = pde.B(state_function_em, state_function_mm)
move(fea_em.mesh, state_function_mm)
with XDMFFile(MPI.COMM_WORLD, "solutions/state_"+state_name_em+".xdmf", "w") as xdmf:
    xdmf.write_mesh(fea_em.mesh)
    fea_em.states_dict[state_name_em]['function'].name = state_name_em
    xdmf.write_function(fea_em.states_dict[state_name_em]['function'])

with XDMFFile(MPI.COMM_WORLD, "solutions/magnetic_flux_density.xdmf", "w") as xdmf:
    xdmf.write_mesh(fea_em.mesh)
    magnetic_flux_density.name = "B"
    xdmf.write_function(magnetic_flux_density)
