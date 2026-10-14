# OperAItor: model and benchmark equations

Every equation used by the simulator, the control and safety layers, fault detection, the agents'
validation and the benchmark score. Each section names the source file. The values given are the
defaults in the code.

Notation: time $t$ in hours inside the plant model, flows in kmol/h, hold-ups in kmol, temperatures
in K, pressures in bar(abs) and enthalpies in kJ/kmol. Component 1 is acetone (light), component 2
is isopropanol, IPA (heavy). $x$ is the liquid mole fraction of acetone and $y$ the vapour mole
fraction.

A smooth positive part is used throughout, so the equations stay differentiable for the solver:

$$
\langle a \rangle = \tfrac12\left(a + \sqrt{a^2 + \varepsilon^2}\right), \qquad \varepsilon = 10^{-10}
$$

---

## 1. Thermodynamics (`operaitor/plant/thermo.py`)

**Vapour pressure** (Antoine, two-parameter form):

$$
\ln P^{\text{sat}}_i(T) = A_i - \frac{B_i}{T}, \qquad A = (10.9386,\ 13.5215),\quad B = (3600,\ 4800)\ \text{K}
$$

**Activity coefficients** (asymmetric Margules surrogate, $A_{12} = 0.20$, $A_{21} = 0.15$):

$$
\ln\gamma_1 = \left[A_{12} + 2(A_{21} - A_{12})\,x_1\right] x_2^2, \qquad
\ln\gamma_2 = \left[A_{21} + 2(A_{12} - A_{21})\,x_2\right] x_1^2
$$

**Bubble pressure and vapour composition** (modified Raoult's law):

$$
P_{\text{bub}}(T, x) = \sum_i x_i\,\gamma_i\,P^{\text{sat}}_i(T), \qquad
y_1 = \frac{x_1\,\gamma_1\,P^{\text{sat}}_1(T)}{P}
$$

**Bubble temperature**: $T_{\text{bub}}(x, P)$ solves $P_{\text{bub}}(T, x) = P$ on $[275, 450]$ K
(Brent's method).

**Enthalpies** ($T_{\text{ref}} = 298.15$ K):

$$
h^L(T, x) = \Big(\sum_i x_i\,c^L_{p,i}\Big)(T - T_{\text{ref}}), \qquad
h^V(T, y) = \Big(\sum_i y_i\,c^V_{p,i}\Big)(T - T_{\text{ref}}) + \sum_i y_i\,\Delta h^{\text{vap}}_i
$$

with $c^L_p = (125, 160)$ and $c^V_p = (80, 100)$ kJ/(kmol K), and
$\Delta h^{\text{vap}} = (30\,000,\ 40\,000)$ kJ/kmol.

**Steam saturation temperature** (Antoine for water, $P$ in mmHg, $T$ in °C):

$$
T_{\text{sat}} = \frac{1810.94}{8.14019 - \log_{10} P_{\text{mmHg}}} - 244.485 + 273.15
$$

---

## 2. Reactor R-101: non-isothermal CSTR (`operaitor/plant/cstr.py`)

The reaction is catalytic dehydrogenation, IPA → acetone + H₂ (liquid phase, endothermic). The
model is a DAE with 4 differential and 5 algebraic states:
$M$ (liquid hold-up), $n_B$ (acetone), $U$ (energy), $n_{H_2}$ (gas), $T$, $x_B$, $P$, $r$ and
$F_{\text{vent}}$.

**Kinetics** (first order in IPA, Arrhenius; $k_{\text{ref}} = 4.667\ \text{h}^{-1}$ at
$T_{\text{ref}} = 335.74$ K, $E_a = 60\,000$ kJ/kmol; $a$ is the catalyst activity, nominally 1):

$$
k(T) = k_{\text{ref}}\exp\!\left[-\frac{E_a}{R}\left(\frac1T - \frac1{T_{\text{ref}}}\right)\right], \qquad
r = a\,k(T)\,\langle M - n_B \rangle
$$

**Material balances** ($F$ is the feed, $F_{\text{out}}$ the outlet flow and $F_{\text{leak}}$ a liquid
leak):

$$
\frac{dM}{dt} = F - (F_{\text{out}} + F_{\text{leak}}), \qquad
\frac{dn_B}{dt} = r - (F_{\text{out}} + F_{\text{leak}})\,x_B, \qquad
\frac{dn_{H_2}}{dt} = r - F_{\text{vent}}
$$

**Energy balance** ($\Delta H_r = 68\,000$ kJ/kmol, endothermic):

$$
\frac{dU}{dt} = F\,h^L(T_F, 0) - (F_{\text{out}} + F_{\text{leak}})\,h^L(T, x_B) - F_{\text{vent}}\,h_{H_2}(T)
+ Q_j - Q_{\text{loss}} - Q_{\text{em}} - r\,\Delta H_r
$$

Heat duties ($u_h$ is the heater factor, $u_{em}$ the emergency-cooling factor):

$$
Q_j = UA_j\,u_h\,\langle T_{\text{hot}} - T \rangle,\quad
Q_{\text{loss}} = UA_{\text{loss}}\,\langle T - T_{\text{amb}} \rangle,\quad
Q_{\text{em}} = UA_{\text{em}}\,u_{em}\,\langle T - T_{cw} \rangle
$$

with $UA_j = 165\,000$, $UA_{\text{loss}} = 2\,500$ and $UA_{\text{em}} = 50\,000$ kJ/(h K), and
$T_{\text{hot}} = 390$ K.

**Algebraic equations:**

$$
x_B = \frac{n_B}{M}, \qquad
U = M\,h^L(T, x_B) + n_{H_2}\,u_{H_2}(T), \qquad
P = \frac{n_{H_2}\,R\,T}{V_g}
$$

where $h_{H_2} = c_{p,H_2}(T - T_{\text{ref}})$, $u_{H_2} = (c_{p,H_2} - R_E)(T - T_{\text{ref}})$,
$c_{p,H_2} = 29$ kJ/(kmol K), $R_E = 8.314$ kJ/(kmol K) in the energy terms and $R = 0.08314$ bar m³/(kmol K)
in the gas law.

**Gas volume** (6 m³ vessel, 0.075 m³/kmol liquid, plus 22.75 m³ connected vent capacitance):

$$
V_g = \max(6 - 0.075\,M,\ 10^{-5}) + 22.75
$$

**Hydrogen relief paths.** The pressure-control valve (PCV), blowdown valve (BDV) and pressure
safety valve (PSV) vent to the flare / fuel-gas header at $P_b = 1.013$ bar (the economics credit the vented
H₂ at fuel value):

$$
F_{\text{vent}} = \left(u_{\text{PCV}}\,C_{\text{PCV}} + u_{\text{BDV}}\,C_{\text{BDV}} + \phi_{\text{PSV}}\,C_{\text{PSV}}\right)\sqrt{\langle P - P_b \rangle}
$$

$$
\phi_{\text{PSV}} = \operatorname{clip}_{[0,1]}\!\left(\frac{\langle P - 4.50 \rangle}{4.70 - 4.50}\right)
$$

with $C_{\text{PCV}} = 99.32$, $C_{\text{BDV}} = 135$ and $C_{\text{PSV}} = 180$ kmol/(h √bar).

**Nominal heater factor**: the value of $u_h$ that closes the steady energy balance at
$T = 335.74$ K and 70 % conversion:

$$
u_h^{\text{nom}} = \frac{F h^L_{\text{out}} + r\,h_{H_2} + r\,\Delta H_r - F h^L_{\text{in}} + Q_{\text{loss}}}{UA_j\,(T_{\text{hot}} - T)}
$$

---

## 3. Column T-201: binary distillation (`operaitor/plant/distillation.py`)

The column has 10 equilibrium trays (feed on tray 6 from the top), a reboiler, a reflux drum, and
one lumped condenser-pressure state. For tray $i$ (numbered from the top), liquid flows in from
tray $i-1$ and vapour from tray $i+1$. The top tray receives the reflux $R$ at the drum
composition, and the bottom tray receives the reboiler vapour.

**Tray balances** ($F_i$ is the feed on the feed tray only, at composition $z$ = reactor $x_B$ and
temperature = reactor $T$):

$$
\frac{dM_i}{dt} = L_{i-1} + V_{i+1} + F_i - L_i - V_i
$$

$$
\frac{dn_i}{dt} = L_{i-1}x_{i-1} + V_{i+1}y_{i+1} + F_i z - L_i x_i - V_i y_i
$$

$$
\frac{dU_i}{dt} = L_{i-1}h^L_{i-1} + V_{i+1}h^V_{i+1} + F_i h_F - L_i h^L_i - V_i h^V_i
$$

**Tray algebraic equations:**

$$
x_i = \frac{n_i}{M_i},\quad U_i = M_i\,h^L(T_i, x_i),\quad P_i = P_{\text{bub}}(T_i, x_i),\quad y_i = y_1(T_i, x_i, P_i)
$$

**Tray hydraulics.** Liquid leaves by a Francis-weir-type law ($M_{\text{nom}} = 2.5$ kmol;
$L^{\text{nom}}_i$ is 80 above the feed and 180 kmol/h below it). Vapour moves up by pressure
difference:

$$
L_i = L^{\text{nom}}_i\left(\frac{\langle M_i \rangle}{M_{\text{nom}}}\right)^{1.5}, \qquad
V_i = C_v\sqrt{\langle P_i - P_{i-1} \rangle}, \quad C_v = \frac{158.89}{\sqrt{0.005}}
$$

with $P_0 \equiv P_c$, the condenser pressure.

**Reboiler** ($B$ is the bottoms flow, $F^{rb}_{\text{leak}}$ a leak and $u_s \in [0, 1]$ the steam-valve opening):

$$
\frac{dM_r}{dt} = L_N - (B + F^{rb}_{\text{leak}}) - V_r, \qquad
\frac{dn_r}{dt} = L_N x_N - (B + F^{rb}_{\text{leak}})x_r - V_r y_r
$$

$$
\frac{dU_r}{dt} = L_N h^L_N - (B + F^{rb}_{\text{leak}})h^L_r - V_r h^V_r + Q_{\text{reb}} - Q^{rb}_{\text{loss}}
$$

$$
Q_{\text{reb}} = UA_{\text{reb}}\,u_s\,\langle T_{\text{sat}}(P_{\text{steam}}) - T_r \rangle, \qquad
V_r = C_v\sqrt{\langle P_r - P_N \rangle}
$$

with $UA_{\text{reb}} = 101\,489$ and $UA^{rb}_{\text{loss}} = 1\,200$ kJ/(h K). $UA_{\text{reb}}$ is sized so the
valve is 60 % open at the design duty; fully open gives 1.69 times the design duty. The reboiler has the
same equilibrium algebraic equations as a tray.

**Condenser.** It condenses at most what the cooling water can remove. $u_c$ is the cooling-water
valve and $\eta$ the cooling effectiveness (the condenser-cooling-loss fault lowers $\eta$):

$$
Q_{\text{avail}} = UA_c\,u_c\,\eta\,\langle T_1 - T_{cw} \rangle, \qquad UA_c = 225\,000\ \text{kJ/(h K)}
$$

$$
F_{\text{cond}} = \min\!\left(\frac{Q_{\text{avail}}}{28\,991},\ \Big\langle V_1 + 80\,(P_c - 1.0) \Big\rangle\right)
$$

**Condenser / top pressure** (vapour capacitance 20 kmol/bar):

$$
\frac{dP_c}{dt} = \frac{V_1 - F_{\text{cond}}}{20}
$$

**Reflux drum** ($D$ is the distillate and $F^{d}_{\text{leak}}$ a leak):

$$
\frac{dM_d}{dt} = F_{\text{cond}} - (R + D + F^{d}_{\text{leak}}), \qquad
\frac{dn_d}{dt} = F_{\text{cond}}\,y_1 - (R + D + F^{d}_{\text{leak}})\,x_d
$$

$$
\frac{dU_d}{dt} = F_{\text{cond}}\,h^V(T_1, y_1) - (R + D + F^{d}_{\text{leak}})\,h^L(T_d, x_d) - F_{\text{cond}}\cdot 29\,583 - Q^d_{\text{loss}}
$$

---

## 4. Integrated plant and numerical solution (`operaitor/plant/plant.py`)

The reactor outlet $F_{\text{out}}$ is the column feed, at the reactor composition $x_B$ and
temperature $T$. The full DAE is

$$
\mathbf{F}(t, \mathbf{y}, \dot{\mathbf{y}}, \mathbf{u}) = \mathbf{0}
$$

It is integrated with **backward Euler** over the 0.1-min plant step. $\mathcal D$ is the set of
differential states, and each step solves

$$
\mathbf{F}\!\left(\mathbf{y}^{n+1},\ \dot{\mathbf{y}}_{\mathcal D} = \frac{\mathbf{y}^{n+1}_{\mathcal D} - \mathbf{y}^{n}_{\mathcal D}}{\Delta t},\ \mathbf{u}\right) = \mathbf{0}
$$

The solver uses Powell's hybrid method. If that fails to converge, the step is halved recursively
up to 5 times (down to $\Delta t/32$), with a least-squares fallback at the smallest step.
Residuals are scaled per equation, for example energy balances by $5$–$6\times10^6$.

---

## 5. Regulatory control, BPCS (`operaitor/plant/control.py`)

Eight PI loops are updated every 1 min:

| Loop | PV | MV | $K_p$ | $T_i$ [h] | Deadband |
|---|---|---|---|---|---|
| R_LIC | reactor hold-up | reactor outflow | −6.0 | 2.0 | 0.50 kmol |
| R_TIC | reactor T | heater factor | +0.030 | 0.12 | 0.30 K |
| R_PIC | reactor P | PCV | −0.10 | 0.30 | 0.015 bar |
| RB_LIC | reboiler hold-up | bottoms flow | −6.0 | 2.0 | 0.35 kmol |
| DR_LIC | drum hold-up | distillate flow | −12.0 | 2.0 | 0.30 kmol |
| COL_PIC | column P | condenser CW valve | −4.5 | 0.20 | 0.002 bar |
| TOP_TIC | top tray T | reflux flow | −7.0 | 0.15 | 0.30 K |
| BOT_TIC | bottom T | steam valve (0–1) | +0.0325 | 0.15 | 0.30 K |

**Positional PI with deadband** ($b$ is the bias, the nominal MV):

$$
e_k = \begin{cases} 0 & |SP - PV_k| \le \delta \\ SP - PV_k & \text{otherwise}\end{cases}
\qquad
u_k = \operatorname{clip}_{[u_{\min}, u_{\max}]}\!\Big(b + K_p e_k + I_k\Big),\quad
I_k = I_{k-1} + \frac{K_p}{T_i}\,e_k\,\Delta t
$$

**Anti-windup (conditional integration):** the integral update is kept only if the output is not
saturated, or if the proportional term pushes the output back inside the limits.

**Bumpless MANUAL → AUTO transfer** (back-calculation while in MANUAL):

$$
I = u_{\text{manual}} - b - K_p e
$$

---

## 6. Equipment, faults and disturbances (`equipment.py`, `scenario.py`, `measurement.py`)

**Measurements** (BPCS; Gaussian noise with a fixed seed):

$$
\tilde y_j = y_j + \nu_j, \qquad \nu_j \sim \mathcal N(0, \sigma_j^2)
$$

**Sensor faults:** a bias gives $\tilde y_j + \beta$, and a freeze holds $\tilde y_j$ at its value
when the fault starts. In the console, the bias is $\beta = y_j \times \text{bias\%}/100$.

**Pump trip:** a failed duty pump delivers $F = 0$ while the controller keeps demanding flow. Starting
the standby pump restores the flow.

**Actuator faults** apply to the actual input $u^{\text{act}}$, after the SIF overrides:

- capacity: $u^{\text{act}} = \min(u^{\text{req}},\ f\,u_{\text{ref}})$;
- capacity ramp: the same with $f(t) = 1 + \pi(t)(f - 1)$, where $\pi(t) = \operatorname{clip}_{[0,1]}\big((t - t_0)/\tau_{\text{ramp}}\big)$;
- freeze: $u^{\text{act}} = u^{\text{req}}(t_0)$;
- fixed: $u^{\text{act}} = u_{\text{fixed}}$;
- stiction: $u^{\text{act}}$ holds its last value until $|u^{\text{req}} - u^{\text{held}}| > \beta_s\,\cdot$ scale.

**Process faults:**

- catalyst activity loss: $a = 1 - s/100$;
- condenser cooling loss: $\eta = 1 - s/100$;
- liquid leaks: $F_{\text{leak}} = s$ kmol/h.

**Disturbance steps:** temperatures are additive ($T \leftarrow T + \Delta$) and pressures are
multiplicative ($P \leftarrow P(1 \pm \Delta)$), applied for a set duration.

---

## 7. Alarms and safety instrumented functions (`alarms.py`, `safety.py`)

**Alarm with hysteresis** $h$ (a high alarm is shown; a low alarm is symmetric):

$$
\text{active}_k = \begin{cases} PV_k \ge L - h & \text{if active}_{k-1} \\ PV_k \ge L & \text{otherwise} \end{cases}
$$

Alarms move through the states NORMAL → ACTIVE_UNACK → ACTIVE_ACK → RTN_UNACK → NORMAL.

**SIF trip** when the true variable crosses its threshold. The table is in
[plant-model.md](plant-model.md#sif--interrupt-layer); for example $P_R \ge 4.35$ bar or a lowest
tray hold-up $\le 0.30$ kmol. SIFs latch. **A reset is accepted only inside the reset band**
($h_r = 0.05$); for a high trip:

$$
x < x_{\text{trip}}\,(1 - h_r)
$$

A *plant-trip* SIF (R_P_HHH, RB_L_HH, DR_L_LL, DR_L_HH, COL_P_HH, COL_L_LL) ends the episode as
SAFE_TRIP.

---

## 8. Fault detection: PCA monitoring (`operaitor/fdd/pca.py`, `operaitor/agents/monitoring.py`)

**Training:** normal-operation data $X$ ($n \times m$) is standardised as
$Z = (X - \bar x)/s$. Its covariance eigen-decomposition gives loadings $P$ ($m \times k$) and
eigenvalues $\Lambda = \operatorname{diag}(\lambda_1..\lambda_k)$. $k$ is the smallest number of
components that explains at least 90 % of the variance.

**Statistics for one observation** $z = (x - \bar x)/s$:

$$
t = z P, \qquad T^2 = t\,\Lambda^{-1} t^\top = \sum_{a=1}^k \frac{t_a^2}{\lambda_a}, \qquad
\text{SPE} = Q = \lVert z - t P^\top \rVert^2
$$

**Control limits** ($\alpha = 0.99$). The theoretical limits are

$$
T^2_{\lim} = \frac{k(n-1)(n+1)}{n(n-k)}\,F_\alpha(k, n-k), \qquad
Q_{\lim} = g\,\chi^2_\alpha(h),\quad g = \frac{\operatorname{var}(Q)}{2\,\overline{Q}},\ h = \frac{2\,\overline{Q}^2}{\operatorname{var}(Q)}
$$

Each limit used is the maximum of its theoretical value and the empirical 99th percentile of the
training statistic, because the training data is not Gaussian (noise plus disturbance steps).

**Contributions** (complete decomposition):

$$
c^{T^2}_i = z_i\,\big(P\Lambda^{-1}P^\top z^\top\big)_i \quad \Big(\textstyle\sum_i c^{T^2}_i = T^2\Big), \qquad
c^{Q}_i = r_i^2,\ r = z - tP^\top
$$

$$
\text{score}_i = \frac{\max(c^{T^2}_i, 0)}{T^2_{\lim}} + \frac{c^Q_i}{Q_{\lim}}
$$

**Detection:** a PCA fault is declared after $T^2 > T^2_{\lim}$ or $Q > Q_{\lim}$ for 3 consecutive
samples (persistence $p = 3$).

**Protocol v2 event trigger.** The agents are called once per fault: when an alarm is active, or
when $Q > Q_{\lim}$ for $p$ consecutive samples. They are called again only on a new priority-1
alarm or a new pump trip.

**Time to the next limit** (linear extrapolation of the current rate):

$$
\tau = \frac{|L - x_k|}{|\dot x_k|}, \qquad \dot x_k \approx \frac{x_k - x_{k-1}}{\Delta t}
$$

---

## 9. Validation of agent actions (`operaitor/agents/recovery_graph.py`, ADMIT)

**Digital twin.** The proposed action is applied to a clone of the plant and simulated for 30 min.
The plant counts as *restored* when every faulty target (active alarm limit, failed pump, and
$T^2$ / SPE if PCA confirmed the fault) has been back within its limit for 2 min. When nothing
restores the plant, candidates are ranked lexicographically (lower is better):

$$
\big(\ \mathbb 1[\text{trip}],\ -t_{\text{trip}},\ \#\text{targets not met},\ \text{fraction of time unsafe},\ T^2/T^2_{\lim} + Q/Q_{\lim}\ \big)
$$

**ADMIT T4 (consequence gate).** The normalised safety margin is the smallest distance to any trip
limit, scaled by the distance from nominal to that limit:

$$
m(s) = \min_{v}\ \frac{L_v - s_v}{|L_v - s^{\text{nom}}_v|}\ \ (\text{high limits}), \qquad
\frac{s_v - L_v}{|L_v - s^{\text{nom}}_v|}\ \ (\text{low limits})
$$

So $m = 1$ at nominal and $m = 0$ at the trip limit. The plan and "do nothing" are both projected
for 30 min. The plan is **rejected** if it crosses a limit ($m \le 0$), or if

$$
\min_t m_{\text{plan}}(t) < \min_t m_{\text{null}}(t) - 0.2
$$

---

## 10. Economics and the benchmark score (`operaitor/benchmark/economics.py`, `profit_campaign.py`)

All quantities use the **true** plant state, at the 0.5-min step.
$M_{\text{ac}} = 58.08$, $M_{\text{IPA}} = 60.10$ and $M_{H_2} = 2.016$ kg/kmol.

**Hourly margin** [USD/h]:

$$
\dot\Pi = \underbrace{\mathbb 1[x_D \ge 0.90]\,D\,x_D\,\tfrac{M_{\text{ac}}}{1000}\,p_{\text{ac}}}_{\text{on-spec acetone}}
+ \underbrace{0.95\,B(1 - x_B)\,\tfrac{M_{\text{IPA}}}{1000}\,p_{\text{IPA}}}_{\text{recycled IPA}}
+ \underbrace{F_{\text{vent}}\,M_{H_2}\,p_{H_2}}_{\text{H}_2\text{ fuel}}
- \underbrace{F\,\tfrac{M_{\text{IPA}}}{1000}\,p_{\text{IPA}}}_{\text{feed}}
- 3.6\,p_{\text{steam}}\,Q_{\text{reb}}^{[\text{MW}]}
- 3.6\,p_{\text{hot}}\,Q_j^{[\text{MW}]}
$$

The prices are $p_{\text{ac}} = 1300$ \$/t, $p_{\text{IPA}} = 1000$ \$/t, $p_{H_2} = 1$ \$/kg and
$p_{\text{steam}} = p_{\text{hot}} = 8$ \$/GJ. Off-spec distillate ($x_D < 0.90$) earns nothing.

**Inventory value** (reactor, reboiler and drum hold-up, valued as IPA):

$$
V_{\text{inv}} = (M_R + M_r + M_d)\,\frac{M_{\text{IPA}}}{1000}\,p_{\text{IPA}}
$$

**Scenario profit** over the window $W$ ($\Delta t = 0.5$ min $= 1/120$ h). The plant earns nothing
after a trip, because the episode ends there:

$$
\Pi_W = \sum_{k:\,t_k \le W} \dot\Pi_k\,\Delta t \;+\; \big(V_{\text{inv}}(t_{\text{end}}) - V_{\text{inv}}(t_1)\big) \;-\; \mathbb 1[\text{plant stop} \le W]\cdot \$20\,000
$$

The \$20,000 penalty is a \$5,000 shutdown plus a \$15,000 restart. A *plant stop* is a plant-trip
SIF, or an episode ending in DAMAGE, MODEL_LIMIT or NUMERICAL_FAILURE.

**Saving** of an agent configuration $c$ against doing nothing on the same scenario:

$$
S_c = \Pi_W^{(c)} - \Pi_W^{(\text{no agents})}
$$

**Fault timing.** The first fault starts at 10 min. A *probe* run with no agents and persistent faults
gives the trip time $t_{\text{trip}}$. Each fault $j$ is then lifted at

$$
t^{\text{lift}}_j = \begin{cases} \max(t_{\text{trip}} + 10,\ t^{\text{on}}_j + 10) & \text{probe tripped} \\ t^{\text{on}}_j + 30 & \text{otherwise}\end{cases}
\qquad
W = \max\!\big(120,\ \max_j t^{\text{lift}}_j + 60\big)\ \text{min}
$$

A shift operator requests a SIF reset every 5 min after the last lift, for as long as any SIF is
latched. The human test in the console uses exactly the same timing, scoring and resets.
