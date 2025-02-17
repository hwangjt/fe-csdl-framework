"""
Definition of the variational form of the motor problem
"""
from fe_csdl_opt.fea.fea_dolfinx import *
from permeability.piecewise_permeability import *


exp_coeff = extractexpDecayCoeff()
cubic_bounds = extractCubicBounds()

# START NEW PERMEABILITY
def RelativePermeability(subdomain, u, uhat):
    gradu = gradx(u,uhat)
    if subdomain == 1 or subdomain == 2: # Electrical/Silicon/Laminated Steel
        B = as_vector([gradu[1], -gradu[0]])
        norm_B = sqrt(dot(B, B) + DOLFIN_EPS)

        mu = conditional(
            lt(norm_B, cubic_bounds[0]),
            linearPortion(norm_B),
            conditional(
                lt(norm_B, cubic_bounds[1]),
                cubicPortion(norm_B),
                (exp_coeff[0] * exp(exp_coeff[1]*norm_B + exp_coeff[2]) + 1)
            )
        )
    elif subdomain >= 3 and subdomain <= 14: # NEODYMIUM
        mu = 1.05
    elif subdomain >= 15 and subdomain <= 50: # COPPER
        mu = 1.00
    elif subdomain == 51: # insert value for titanium or shaft material
        mu = 1.00
    elif subdomain >= 52: # AIR
        mu = 1.00
    return mu
# END NEW PERMEABILITY

def compute_i_abc(iq, angle=0.0):
    i_abc = as_vector([
        iq * np.sin(angle),
        iq * np.sin(angle - 2*np.pi/3),
        iq * np.sin(angle + 2*np.pi/3),
    ])
    return i_abc

def JS(v,uhat,iq,p,s,Hc,angle):
    """
    The variational form for the source term (current) of the
    Maxwell equation
    """
    Jm = 0.
    gradv = gradx(v,uhat)
    base_magnet_dir = 2 * np.pi / p / 2
    magnet_sweep    = 2 * np.pi / p
    for i in range(p):
        flux_angle = base_magnet_dir + i * magnet_sweep
        Hx = (-1)**(i) * Hc * np.cos(flux_angle + angle*2/p)
        Hy = (-1)**(i) * Hc * np.sin(flux_angle + angle*2/p)

        H = as_vector([Hx, Hy])

        curl_v = as_vector([gradv[1],-gradv[0]])
        Jm += inner(H,curl_v)* J(uhat) *dx(i + 2 + 1)

    num_phases = 3
    num_windings = s
    coil_per_phase = 2
    stator_winding_index_start  = p + 2 + 1
    stator_winding_index_end    = stator_winding_index_start + num_windings
    Jw = 0.
    i_abc = compute_i_abc(iq, angle)
    JA, JB, JC = i_abc[0] + DOLFIN_EPS, i_abc[1] + DOLFIN_EPS, i_abc[2] + DOLFIN_EPS


    coils_per_pole  = 3
    for i in range(p): # assigning current densities for each set of poles
        coil_start_ind  = stator_winding_index_start + i * coils_per_pole
        coil_end_ind    = coil_start_ind + coils_per_pole

        J_list = [
            JB * (-1)**(i+1) * v * J(uhat) * dx(coil_start_ind),
            JA * (-1)**(i) * v * J(uhat) * dx(coil_start_ind + 1),
            JC * (-1)**(i+1) * v * J(uhat) * dx(coil_start_ind + 2),
        ]
        Jw += sum(J_list)

    return Jm + Jw


def pdeResEM(u,v,uhat,iq,dx,p,s,Hc,vacuum_perm,angle,
                g=None,nitsche=False, sym=False, overpenalty=False,ds_=ds):
    """
    The variational form of the PDE residual for the electromagnetic problem
    """
    res = 0.
    gradu = gradx(u,uhat)
    gradv = gradx(v,uhat)

    num_components = 4 * 3 * p + 2 * s
    for i in range(num_components):
        res += 1./vacuum_perm*(1/RelativePermeability(i + 1, u, uhat))\
                *dot(gradu,gradv)*J(uhat)*dx(i + 1)
    res -= JS(v,uhat,iq,p,s,Hc,angle)

    mesh = u.function_space.mesh
    boundary_res = 0.
    if nitsche is True:
        beta = 1e4
        sgn = 1.0
        if sym is not True:
            sgn = -1.0
        n = FacetNormal(mesh)
        # transform normal and area element by Nanson's formula:
        dsx_dsy_n_x = J(uhat)*inv(F(uhat).T)*n
        norm_dsx_dsy_n_x = ufl.sqrt(ufl.dot(dsx_dsy_n_x, dsx_dsy_n_x))

        h_E = CellDiameter(mesh)
        boundary_components = [0,1]
        for i in boundary_components:
            coeff = 1./vacuum_perm*(1/RelativePermeability(i + 1, u, uhat))
            nitsche_term = coeff*(- inner(dot(gradu,dsx_dsy_n_x),v) \
                            - sgn*inner(dot(gradv,dsx_dsy_n_x),u-g))*ds_
            boundary_res += nitsche_term

            penalty_term = beta/h_E*coeff*norm_dsx_dsy_n_x*inner(v,u-g)*ds_
            if sym is True or overpenalty is True:
                boundary_res += penalty_term

    res += boundary_res
    return res

I = Identity(2)

def pdeResMM(uhat, duhat, g=None,
            nitsche=False, sym=False, overpenalty=False,
            dS_=dS, ds_=ds):
    """
    Formulation of mesh motion as a hyperelastic problem
    """
    mesh = uhat.function_space.mesh
    # Residual for mesh, which satisfies a fictitious elastic problem:
    def _F(u):
        return grad(u)+I
    def _sigma(u):
        F = _F(u)
        E = 0.5*(F.T*F-I)
        m_jac_stiff_pow = 3
        # Artificially stiffen the mesh where it is getting crushed:
        K = 1/pow(det(F),m_jac_stiff_pow)
        mu = 1/pow(det(F),m_jac_stiff_pow)
        S = K*tr(E)*I + 2.0*mu*(E - tr(E)*I/3.0)
        return S
    def P(u):
        return _F(u)*_sigma(u)

    F_m = _F(uhat)
    S_m = _sigma(uhat)
    P_m = P(uhat)
    dS_m = _sigma(duhat)
    res_m = (inner(P_m,grad(duhat)))*dx


    if nitsche is True:
        beta = 5e3/pow(det(F_m),3)
        sgn = 1.0
        if sym is not True:
            sgn = -1.0
        n = FacetNormal(mesh)
        h_E = CellDiameter(mesh)
        f0 = -div(P(g))
        res_m += -dot(f0, duhat)*dx
        nitsche_1 = - inner(dot(P_m,n),duhat)
        nitsches_term_1 = nitsche_1("+")*dS_ + nitsche_1("-")*dS_ + nitsche_1*ds_
        dP = derivative(P_m, uhat, duhat)
        nitsche_2 = sgn * inner(dP*n,uhat-g)
        nitsches_term_2 = nitsche_2("+")*dS_ + nitsche_2("-")*dS_ + nitsche_2*ds_
        penalty = beta/h_E*inner(duhat,uhat-g)
        penalty_term = penalty("+")*dS_ + penalty("-")*dS_ + penalty*ds_
        res_m += nitsches_term_1
        res_m += nitsches_term_2
        if sym is True or overpenalty is True:
            res_m += penalty_term
    return res_m


def B_power_form(A_z, uhat, n, dx, subdomains):
    """
    Return the ufl form of `B**n*dx(subdomains)`
    """

    mesh = uhat.function_space.mesh
    gradA_z = gradx(A_z,uhat)
    B_power_form = 0.
    B_magnitude = sqrt(gradA_z[0]**2+gradA_z[1]**2)
    for subdomain_id in subdomains:
        B_power_form += pow(B_magnitude, n)*J(uhat)*dx(subdomain_id)
    return B_power_form

def area_form(uhat, dx, subdomains):
    """
    Return the ufl form of `uhat*dx(subdomains)`
    """
    if type(subdomains) == int:
        subdomain_group = [subdomains]
    else:
        subdomain_group = subdomains
    area = 0
    for subdomain_id in subdomain_group:
        area += J(uhat)*dx(subdomain_id)
    return area

def B(A_z, uhat):
    gradA_z = gradx(A_z,uhat)
    B_form = as_vector((gradA_z[1], -gradA_z[0]))
    # dB_dAz = derivative(B_form, state_function_em)

    mesh = uhat.function_space.mesh
    VB = VectorFunctionSpace(mesh,('DG',0))
    B = Function(VB)
    project(B_form,B)
    return B

def getFuncAverageSubdomain(func, uhat, dx, subdomain):
    """
    Compute the average function value over a subdomain
    """
    func_unit = Function(func.function_space)
    func_unit.vector.set(1.0)
    integral = inner(func, func_unit)*J(uhat)*dx(subdomain)
    area = area_form(uhat, dx, subdomain)
    print('subdomain:', subdomain)
    print('area:', assemble(area))
    avg_func = assemble(integral)/assemble(area)
    print('avg func over subdomain:', avg_func)
    print('avg func over subdomain:', assemble(avg_func))
    return avg_func

# TODO
def getFuncAverageSubdomainDerivatives(func, uhat, dx, subdomain):
    '''
    Get the partial derivatives of the area-integrated function
    w.r.t. A_z and uhat
    '''
    F = getFuncAverageSubdomain(func, subdomain)
    dFdAz = derivative(F, func)
    dFdAz_array = assemble(dFdAz).get_local()
    dFduhat =  derivative(F, uhat)
    dFduhat_array = assemble(dFduhat).get_local()

    return dFdAz_array, dFduhat_array

def calcAreaIntegratedAz(A_z, uhat, dx, slot_subdomains):
    """
    Compute the average function value over a subdomain
    """
    A_bar_slot = np.zeros(len(slot_subdomains),)
    for i, ind in enumerate(slot_subdomains):
        A_bar_slot_ind = getFuncAverageSubdomain(A_z,uhat,dx,ind)
        A_bar_slot[i] = A_bar_slot_ind
    return A_bar_slot

# TODO
def calcAreaIntegratedAzDerivatives(A_z, uhat, dx, subdomain):
    dFdAz_array, dFduhat_array = getFuncAverageSubdomainDerivatives(A_z, uhat, dx, subdomain)
    return dFdAz_array, dFduhat_array
