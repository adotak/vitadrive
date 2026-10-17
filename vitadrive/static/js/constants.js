export const VITALS = [
  ["engine_oil_level_pct","Oil level","%"],["engine_oil_life_pct","Oil life","%"],["engine_oil_pressure_kpa","Oil pressure","kPa"],
  ["coolant_temp_c","Coolant temp","°C"],["coolant_level_pct","Coolant level","%"],["transmission_fluid_temp_c","Transmission temp","°C"],
  ["washer_fluid_level_pct","Washer fluid","%"],["brake_fluid_level_pct","Brake fluid","%"],["brake_pad_front_mm","Front pads","mm"],
  ["brake_pad_rear_mm","Rear pads","mm"],["tire_pressure_fl_kpa","Front left","kPa"],["tire_pressure_fr_kpa","Front right","kPa"],
  ["tire_pressure_rl_kpa","Rear left","kPa"],["tire_pressure_rr_kpa","Rear right","kPa"],["tire_tread_mm","Tread depth","mm"],
  ["battery_12v_voltage","12V battery","V"],["hv_battery_soc_pct","HV charge","%"],["hv_battery_soh_pct","HV health","%"],
  ["hv_battery_temp_c","HV temp","°C"],["fuel_level_pct","Fuel","%"],
];
export const SYSTEMS = [
  ["Engine & fluids", ["engine_oil_level_pct","engine_oil_life_pct","engine_oil_pressure_kpa","coolant_temp_c","coolant_level_pct",
    "transmission_fluid_temp_c","washer_fluid_level_pct"]],
  ["Brakes", ["brake_fluid_level_pct","brake_pad_front_mm","brake_pad_rear_mm"]],
  ["Tires", ["tire_pressure_fl_kpa","tire_pressure_fr_kpa","tire_pressure_rl_kpa","tire_pressure_rr_kpa","tire_tread_mm"]],
  ["Battery & energy", ["battery_12v_voltage","hv_battery_soc_pct","hv_battery_soh_pct","hv_battery_temp_c","fuel_level_pct"]],
];
export const SEV = ["ok", "info", "warning", "critical"];
export const HEADLINE = {ok: "All good", info: "Looking good", warning: "Check soon", critical: "Needs attention"};
export const GLYPH = {ok: "✓", info: "✓", warning: "!", critical: "!"};
export const STATE = {ok: "Good", info: "Good", warning: "Check", critical: "Act now"};
export const NEAR_KINDS = {mechanic: "Mechanic", tyres: "Tire shop", parts: "Parts store", dealer: "Car dealer", charging: "EV charger"};
export const GEO_ERR = {
  1: "Location is blocked for this site. Allow it from the icon in the address bar, or type a city or postcode.",
  2: "Your device couldn't work out where you are. Type a city or postcode instead.",
  3: "Finding your location took too long. Try again, or type a city or postcode.",
};
export const worst = list => list.reduce((a, b) => SEV.indexOf(b) > SEV.indexOf(a) ? b : a, "ok");
export const label = k => VITALS.find(([m]) => m === k)?.[1] || k;
