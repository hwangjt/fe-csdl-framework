from csdl import Model
from fe_csdl_opt.csdl_opt.state_model import StateModel
from fe_csdl_opt.csdl_opt.output_model import OutputModel

class FEAModel(Model):
    def initialize(self):
        self.parameters.declare('fea')

    def define(self):
        self.fea_list = fea_list = self.parameters['fea']
        for fea in fea_list:
            for state_name in fea.states_dict:
                arg_name_list_state = fea.states_dict[state_name]['arguments']
                state_model = StateModel(fea=fea,
                                            debug_mode=False,
                                            state_name=state_name,
                                            arg_name_list=arg_name_list_state)
                promote_list = arg_name_list_state.copy()
                self.add(state_model,
                        name='{}_state_model'.format(state_name))

            for output_name in fea.outputs_dict:
                arg_name_list_output = fea.outputs_dict[output_name]['arguments']
                output_model = OutputModel(fea=fea,
                                            output_name=output_name,
                                            arg_name_list=arg_name_list_output)

                promote_list = arg_name_list_output.copy()
                self.add(output_model,
                        name='{}_output_model'.format(output_name))
