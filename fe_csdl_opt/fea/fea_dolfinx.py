"""
The FEniCS wrapper for variational forms and partial derivatives computation
"""

from fe_csdl_opt.fea.utils_dolfinx import *
from dolfinx.io import XDMFFile
import ufl

from dolfinx.fem.petsc import apply_lifting
from dolfinx.fem import (set_bc, Function, FunctionSpace, dirichletbc,
                        locate_dofs_topological, locate_dofs_geometrical,
                        Constant, VectorFunctionSpace)
from ufl import (grad, SpatialCoordinate, CellDiameter, FacetNormal,
                    div, Identity)
import matplotlib.pyplot as plt
from scipy.sparse import csr_matrix

import os.path


class AbstractFEA(object):
    """
    The abstract class of the FEniCS wrapper for defining the variational forms
    for PDE residuals and outputs, computing derivatives, and solving
    the problems.
    """
    def __init__(self, **args):

        self.mesh = None
        self.sym_nitsche = False
        self.initFunctionSpace(self.mesh)
        self.res = None

    def __init__(self, mesh):
        self.mesh = mesh

        self.inputs_dict = dict()
        self.states_dict = dict()
        self.outputs_dict = dict()
        self.bcs_list = list()


    def add_strong_bc(self, bc):
        self.bcs_list.append(bc)

    def add_input(self, name, function):
        if name in self.inputs_dict:
            raise ValueError('name has already been used for an input')

        function.rename(name, name)
        self.inputs_dict[name] = dict(
            function=function,
        )

    def add_state(self, name, function, residual_form, *arguments):
        function.rename(name, name)
        self.states_dict[name] = dict(
            function=function,
            residual_form=residual_form,
            arguments=arguments,
        )

    def add_output(self, name, form, *arguments):
        self.outputs_dict[name] = dict(
            form=form,
            arguments=arguments,
        )


class FEA(object):
    """
    The class of the FEniCS wrapper for the motor problem,
    with methods to compute the variational forms, partial derivatives,
    and solve the nonlinear/linear subproblems.
    """
    def __init__(self, mesh):

        self.mesh = mesh


        self.inputs_dict = dict()
        self.states_dict = dict()
        self.outputs_dict = dict()
        self.bc = []

        self.PDE_SOLVER = "Newton"
        self.REPORT = True

        self.ubc = None
        self.custom_solve = None

        self.opt_iter = 0
        self.record = False
        self.initial_solve = True

        self.recorder_path = "records"

    def add_input(self, name, function):
        if name in self.inputs_dict:
            raise ValueError('name has already been used for an input')
        self.inputs_dict[name] = dict(
            function=function,
            function_space=function.function_space,
            shape=len(getFuncArray(function)),
            recorder=self.createRecorder(name, self.record)
        )

    def add_state(self, name, function, residual_form, arguments,
                    dR_du=None, dR_df_list=[]):

        self.states_dict[name] = dict(
            function=function,
            residual_form=residual_form,
            function_space=function.function_space,
            shape=len(getFuncArray(function)),
            d_residual=Function(function.function_space),
            d_state=Function(function.function_space),
            dR_du=dR_du,
            dR_df_list=dR_df_list,
            arguments=arguments,
            recorder=self.createRecorder(name, self.record)
        )

    def add_output(self, name, type, form, arguments):
        if type == 'field':
            shape = len(getFormArray(form))
        elif type == 'scalar':
            shape = 1
        partials = []
        for argument in arguments:
            if argument in self.inputs_dict:
                partial = derivative(form, self.inputs_dict[argument]['function'])
            elif argument in self.states_dict:
                partial = derivative(form, self.states_dict[argument]['function'])
            partials.append(partial)
        self.outputs_dict[name] = dict(
            form=form,
            shape=shape,
            arguments=arguments,
            partials=partials,
        )

    def add_exact_solution(self, Expression, function_space):
        f_analytic = Expression()
        f_ex = Function(function_space)
        f_ex.interpolate(f_analytic.eval)
        return f_ex

    def add_strong_bc(self, ubc, locate_BC_list,
                    function_space=None):
        if function_space == None:
            for locate_BC in locate_BC_list:
                self.bc.append(dirichletbc(ubc, locate_BC))
        else:
            for locate_BC in locate_BC_list:
                self.bc.append(dirichletbc(ubc, locate_BC, function_space))

    def solve(self, res, func, bc):
        """
        Solve the PDE problem
        """
        solver_type=self.PDE_SOLVER
        report=self.REPORT
        if self.custom_solve is not None and self.initial_solve == True:
            self.custom_solve(res,func,bc,report)
            # self.initial_solve = False
        else:
            solveNonlinear(res,func,bc,solver_type,report)


    def solveLinearFwd(self, du, A, dR, dR_array):
        """
        solve linear system dR = dR_du (A) * du in DOLFIN type
        """
        setFuncArray(dR, dR_array)

        du.vector.set(0.0)

        solveKSP(A, dR.vector, du.vector)
        du.vector.assemble()
        du.vector.ghostUpdate()
        return du.vector.getArray()

    def solveLinearBwd(self, dR, A, du, du_array):
        """
        solve linear system du = dR_du.T (A_T) * dR in DOLFIN type
        """
        setFuncArray(du, du_array)

        dR.vector.set(0.0)
        solveKSP(transpose(A), du.vector, dR.vector)
        dR.vector.assemble()
        dR.vector.ghostUpdate()
        return dR.vector.getArray()

    def createRecorder(self, name, record=True):
        recorder = None
        if record:
            recorder = XDMFFile(MPI.COMM_WORLD,
                                self.recorder_path+"/record_"+name+".xdmf", "w")
            recorder.write_mesh(self.mesh)
        return recorder
