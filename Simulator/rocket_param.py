import numpy as np
from scipy import interpolate
from scipy.integrate import odeint
import pymap3d as pm

import Simulator.coordinate as coord
import Simulator.environment as env
from Simulator.result_plot import Result
from Simulator.result_plot import PayloadResult

class Payload:
    def __init__(self, mass, CdS, result_dir):
        self.result = PayloadResult(result_dir)
        self.mass = mass
        self.CdS = CdS


class Rocket:
    def __init__(self, json, json_engine, result_dir):
        self.result = Result(result_dir)
        launch_pad = json.get('Launch Pad')
        struct = json.get('Structure')
        para = json.get('Parachute')
        aero = json.get('Aero')
        engine = json_engine.get('Engine')
        prop = json_engine.get('Propellant')

        # geometory ################################################
        self.L = struct.get('Length [m]')
        self.d = struct.get('Diameter [m]')
        self.A = 0.25 * np.pi * self.d ** 2

        self.dth = engine.get('Nozzle Throat Diameter [m]')
        self.eps = engine.get('Nozzle Expansion Ratio')
        self.Ath = 0.25 * np.pi * self.dth ** 2
        self.Ae = self.Ath * self.eps
        self.de = np.sqrt(self.Ae * 4.0 / np.pi)
        self.d_out_f = prop.get('Fuel Outside Diameter [m]')
        self.d_port_f = prop.get('Fuel Inside Diameter [m]')
        self.L_f = prop.get('Fuel Length [m]')
        ############################################################


        # Mass parameter ###########################################
        self.m_dry = struct.get('Dry Mass [kg]')
        self.m0_f = prop.get('Fuel Mass Before Burn [kg]')
        self.m0_ox = prop.get('Oxidizer Mass [kg]')
        self.m0_p = self.m0_ox + self.m0_f
        self.ms = self.m_dry - self.m0_f
        self.m0 = self.ms + self.m0_ox
        self.delta_mf = prop.get('Delta Fuel Mass [kg]')
        self.mf_after = self.m0_f - self.delta_mf
        ############################################################


        # Thrust, SL ###############################################
        # 1st column : time [s]
        # 2nd column : thrust [N]
        thrust_file_exist = engine.get('File Exist')
        if thrust_file_exist:
            thrust_file = engine.get('Thrust File')
            thrust_array = np.loadtxt(thrust_file, delimiter=',', skiprows=1)
            time_array = thrust_array[:,0]
            thrust_array = thrust_array[:,1]
            index = (thrust_array > (self.ms + self.m0_f + self.m0_ox) * 9.80665).argmax()  # 1G Cut
            thrust_array = thrust_array[index:]
            time_array = time_array[index:] - time_array[index]
            self.ref_thrust = np.max(thrust_array)
            self.thrust = interpolate.interp1d(time_array, thrust_array, kind='linear', bounds_error=False, fill_value=(0.0, 0.0))
            self.total_impulse = np.sum(thrust_array[1:] * (time_array[1:] - time_array[:-1]))
        else:
            time_array = np.arange(0.0, engine.get('Burn Time [sec]'), 0.01)
            thrust_array = np.array([engine.get('Constant Thrust [N]')] * time_array.size)
            self.ref_thrust = engine.get('Constant Thrust [N]')
            self.thrust = interpolate.interp1d(time_array, thrust_array, kind='linear', bounds_error=False, fill_value=(0.0, 0.0))
            self.total_impulse = round(engine.get('Constant Thrust [N]') * engine.get('Burn Time [sec]'))
        self.burn_time = engine.get('Burn Time [sec]')
        self.Isp = engine.get('Isp [sec]')
        ############################################################


        # Mass interpolate #########################################
        self.mdot_f = prop.get('Fuel Mass Flow Rate [kg/s]')
        mdot_p_log = thrust_array / (self.Isp * 9.80665)
        self.mdot_p = interpolate.interp1d(time_array, mdot_p_log, kind='linear', bounds_error=False, fill_value=(0.0, 0.0))

        time_array_mdotf = np.arange(0.0, self.burn_time, 0.01)
        mdot_f_log = np.array([self.mdot_f]*len(time_array_mdotf))
        self.mdot_f = interpolate.interp1d(time_array_mdotf, mdot_f_log, kind='linear', bounds_error=False, fill_value=(0.0, 0.0))
        mdot_ox_log = np.zeros_like(time_array)
        for i in range(len(time_array)):
            mdot_ox_log[i] = mdot_p_log[i] - self.mdot_f(time_array[i])
        self.mdot_ox = interpolate.interp1d(time_array, mdot_ox_log, kind='linear', bounds_error=False, fill_value=(0.0, 0.0))

        mf_log_ode = odeint(lambda x, t: -self.mdot_f(t), self.m0_f, time_array)  # time_arrayは上のthrust部分参照
        mf_log = mf_log_ode[0][0]
        for mf in mf_log_ode[1:]:
            mf_log = np.append(mf_log, mf[0])  # odeintの出力はndarrayに1要素ndarrayが入ってるので修正
        if np.abs((mf_log[-1] - self.mf_after) / self.mf_after) > 0.05:
            print('Warning!! fuel mass at burn out is not matching')
        self.mf = interpolate.interp1d(time_array, mf_log, kind='linear', bounds_error=False, fill_value=(mf_log[0], mf_log[-1]))

        mox_log_ode = odeint(lambda x, t: -0.0, self.m0_ox, time_array)
        mox_log = mox_log_ode[0][0]
        for mox in mox_log_ode[1:]:
            if mox >= 0.0:
                mox_log = np.append(mox_log, mox[0])  # odeintの出力はndarrayに1要素ndarrayが入ってるので修正
            else:
                mox_log = np.append(mox_log, 0.0)
        self.mox = interpolate.interp1d(time_array, mox_log, kind='linear', bounds_error=False, fill_value=(mox_log[0], 0.0))

        mp_log = mf_log
        self.mp = interpolate.interp1d(time_array, mp_log, kind='linear', bounds_error=False, fill_value=(mp_log[0], mp_log[-1]))

        m_log = self.ms + mp_log
        self.m = interpolate.interp1d(time_array, m_log, kind='linear', bounds_error=False, fill_value=(m_log[0], m_log[-1]))
        self.m_burnout = m_log[-1]
        ############################################################


        # Center of Gravity parameter ##############################
        self.Lcg_dry = struct.get('Dry Length-C.G. [m]')
        self.Lcg0_ox = struct.get('Initial Oxidizer Length-C.G. [m]')
        self.Lcg_f = struct.get('Fuel Length-C.G. [m]')
        self.Lcg_s = (self.m_dry * self.Lcg_dry - self.m0_f * self.Lcg_f) / self.ms  # Structure
        self.L_motor = struct.get('Length End-to-Tank [m]')
        self.Lcg0_p = (self.m0_f * self.Lcg_f) / self.m0_p
        self.Lcg0 = (self.ms * self.Lcg_s + self.m0_p * self.Lcg0_p) / self.m0

        Lcg_ox_log = self.L_motor + (mox_log / self.m0_ox) * (self.Lcg0_ox - self.L_motor)
        Lcg_p_log = (mf_log * self.Lcg_f) / mp_log
        Lcg_log = (mp_log * Lcg_p_log + self.ms * self.Lcg_s) / m_log
        self.Lcg_ox = interpolate.interp1d(time_array, Lcg_ox_log, kind='linear', bounds_error=False, fill_value=(Lcg_ox_log[0], Lcg_ox_log[-1]))
        self.Lcg_p = interpolate.interp1d(time_array, Lcg_p_log, kind='linear', bounds_error=False, fill_value=(Lcg_p_log[0], Lcg_p_log[-1]))
        self.Lcg = interpolate.interp1d(time_array, Lcg_log, kind='linear', bounds_error=False, fill_value=(Lcg_log[0], Lcg_log[-1]))
        ############################################################

        # Launcher lug parameter ######################################
        self.tipoff_exist = struct.get('Tip-Off Calculation Exist')
        if self.tipoff_exist:
            self.upper_lug = struct.get('Upper Launch Lug [m]')
            self.lower_lug = struct.get('Lower Launch Lug [m]')
        else:
            self.lower_lug = self.Lcg0  # 初期重心→簡易的なモデルと大体揃うはず
            self.upper_lug = self.lower_lug + 0.1
        ############################################################

        # Inertia Moment parameter #################################
        self.Ij0_dry_roll = struct.get('Dry Inertia-Moment Roll-Axis [kg*m^2]')
        self.Ij0_dry_pitch = struct.get('Dry Inertia-Moment Pitch-Axis [kg*m^2]')
        Ij_f_pitch_log = mf_log * ((self.d_port_f ** 2 + self.d_out_f ** 2) / 4.0 + self.L_f / 3.0) / 4.0
        Ij_f_roll_log = mf_log * (self.d_port_f ** 2 + self.d_out_f ** 2) / 8.0
        # Offset
        Ij_dry_pitch_log = self.Ij0_dry_pitch + self.m_dry * np.abs(Lcg_log - self.Lcg_dry) ** 2
        Ij_f_pitch_log += mf_log * np.abs(Lcg_log - self.Lcg_f) ** 2
        Ij_pitch_log = Ij_dry_pitch_log - (Ij_f_pitch_log[0] - Ij_f_pitch_log)
        Ij_roll_log = self.Ij0_dry_roll - (Ij_f_roll_log[0] - Ij_f_roll_log)
        self.Ij_pitch = interpolate.interp1d(time_array, Ij_pitch_log, kind='linear', bounds_error=False, fill_value=(Ij_pitch_log[0], Ij_pitch_log[-1]))
        self.Ij_roll = interpolate.interp1d(time_array, Ij_roll_log, kind='linear', bounds_error=False, fill_value=(Ij_roll_log[0], Ij_roll_log[-1]))
        Ijdot_f_pitch_log = Ij_f_pitch_log * self.mdot_f(time_array) / mf_log
        Ijdot_f_roll_log = Ij_f_roll_log * self.mdot_f(time_array) / mf_log
        self.Ijdot_f_pitch = interpolate.interp1d(time_array, Ijdot_f_pitch_log, kind='linear', bounds_error=False, fill_value=(Ijdot_f_pitch_log[0], Ijdot_f_pitch_log[-1]))
        self.Ijdot_f_roll = interpolate.interp1d(time_array, Ijdot_f_roll_log, kind='linear', bounds_error=False, fill_value=(Ijdot_f_roll_log[0], Ijdot_f_roll_log[-1]))
        ############################################################


        # Aero parameter ###########################################
        Cd_file_exist = aero.get('Cd File Exist')
        if Cd_file_exist:
            Cd_file = aero.get('Cd File')
            Cd_array = np.loadtxt(Cd_file, delimiter=',', skiprows=1)
            self.Cd = interpolate.interp1d(Cd_array[:,0], Cd_array[:,1], kind='linear', bounds_error=False, fill_value=(Cd_array[0,1], Cd_array[-1,1]))
        else:
            Mach_array = np.arange(0.0, 21.0, 1.0)
            Cd_array = np.array([aero.get('Constant Cd')] * Mach_array.size)
            self.Cd = interpolate.interp1d(Mach_array, Cd_array, kind='linear', bounds_error=False, fill_value=(Cd_array[0], Cd_array[-1]))

        Lcp_file_exist = aero.get('Lcp File Exist')
        if Lcp_file_exist:
            Lcp_file = aero.get('Lcp File')
            Lcp_array = np.loadtxt(Lcp_file, delimiter=',', skiprows=1)
            Lcp_array[:,1] = self.L - Lcp_array[:,1]
            self.Lcp = interpolate.interp1d(Lcp_array[:,0], Lcp_array[:,1], kind='linear', bounds_error=False, fill_value=(Lcp_array[0,1], Lcp_array[-1,1]))
        else:
            Mach_array = np.arange(0.0, 21.0, 1.0)
            Lcp_array = np.array([aero.get('Constant Length-C.P. from Nosetip [m]')] * Mach_array.size)
            Lcp_array = self.L - Lcp_array
            self.Lcp = interpolate.interp1d(Mach_array, Lcp_array, kind='linear', bounds_error=False, fill_value=(Lcp_array[0], Lcp_array[-1]))

        CNa_file_exist = aero.get('CNa File Exist')
        if CNa_file_exist:
            CNa_file = aero.get('CNa File')
            CNa_array = np.loadtxt(CNa_file, delimiter=',', skiprows=1)
            self.CNa = interpolate.interp1d(CNa_array[:,0], CNa_array[:,1], kind='linear', bounds_error=False, fill_value=(CNa_array[0,1], CNa_array[-1,1]))
        else:
            Mach_array = np.arange(0.0, 21.0, 1.0)
            CNa_array = np.array([aero.get('Constant Normal Coefficient CNa')] * Mach_array.size)
            self.CNa = interpolate.interp1d(Mach_array, CNa_array, kind='linear', bounds_error=False, fill_value=(CNa_array[0], CNa_array[-1]))

        self.Clp = aero.get('Roll Dumping Moment Coefficient Clp')
        if self.Clp > 0.0:
            self.Clp *= -1.0
        self.Cmq = aero.get('Pitch Dumping Moment Coefficient Cmq')
        if self.Cmq > 0.0:
            self.Cmq *= -1.0
        self.Cnr = self.Cmq
        ############################################################


        # Parachute parameter ######################################
        self.timer_mode = para.get('Timer Mode')
        self.CdS1 = para.get('1st Parachute CdS [m2]')
        self.para2_exist = para.get('2nd Parachute Exist')
        if self.para2_exist:
            self.CdS2 = para.get('2nd Parachute CdS [m2]')
            self.alt_sepa2 = para.get('2nd Parachute Opening Altitude [m]')
        else:
            self.CdS2 = 0.0
            self.alt_sepa2 = 0.0

        if self.timer_mode:
            self.t_1st = para.get('1st Timer [s]')
            self.t_2nd_min = para.get('2nd Timer Min [s]')
            self.t_2nd_max = para.get('2nd Timer Max [s]')
        else:
            self.t_1st = 0.0
            self.t_2nd_min = 0.0
            self.t_2nd_max = 9999.0
        ############################################################

        # for Solver #########################################
        self.auto_end = json.get('Solver').get('Auto End Time')
        self.end_time = json.get('Solver').get('End Time [sec]')
        self.time_step = json.get('Solver').get('Time Step [sec]')
        ################################################

        # Initial Condition #############################
        self.azimuth0 = launch_pad.get('Launch Azimuth [deg]')
        self.elevation0 = launch_pad.get('Launch Elevation [deg]')
        self.launch_date = pm.timeconv.str2dt(launch_pad.get('Date'))  # datetime
        self.pos0_LLH = launch_pad.get('Site')  # lat, lon, height
        self.launcher_rail = launch_pad.get('Launcher Rail Length [m]')

        self.input_mag_dec = launch_pad.get('Input Magnetic Azimuth')
        if self.input_mag_dec:
            self.mag_dec = env.magnetic_declination(self.pos0_LLH[0], self.pos0_LLH[1])
            if self.azimuth0 - self.mag_dec < 0.0:
                self.azimuth0 = 360.0 + (self.azimuth0 - self.mag_dec)
            else:
                self.azimuth0 -= self.mag_dec
        ################################################

        # Wind #########################################
        if json.get('Wind').get('Wind File Exist'):
            wind_file = json.get('Wind').get('Wind File')
            wind_array = np.loadtxt(wind_file, delimiter=',', skiprows=1)
            alt_array = wind_array[:, 0]  # [m]
            wind_speed_array = wind_array[:, 1]
            wind_direction_array = wind_array[:, 2]
        else:
            alt_array = [0.0,10000.0, 20000.0]
            wind_speed_array = [0.0, 0.0, 0.0]
            wind_direction_array = [0.0, 0.0, 0.0]

        if self.input_mag_dec:
            for i in range(len(wind_direction_array)):
                if self.azimuth0 - self.mag_dec < 0.0:
                    wind_direction_array[i] = 360.0 + (wind_direction_array[i] - self.mag_dec)
                else:
                    wind_direction_array[i] = wind_direction_array[i] - self.mag_dec

        self.wind_speed = interpolate.interp1d(alt_array, wind_speed_array, kind='linear', bounds_error=False, fill_value=(wind_speed_array[0], wind_speed_array[-1]))
        self.wind_direction = interpolate.interp1d(alt_array, wind_direction_array, kind='linear', bounds_error=False, fill_value=(wind_direction_array[0], wind_direction_array[-1]))
        ################################################

        # Payload #########################################
        self.payload_exist = json.get('Payload').get('Payload Exist')
        if self.payload_exist:
            self.payload = Payload(json.get('Payload').get('Mass [kg]'), json.get('Payload').get('Parachute CdS [m2]'), result_dir)
