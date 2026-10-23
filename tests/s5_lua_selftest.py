r"""S5 第二部分离线自测：CufBridge 0.6.9 真拾取双通道（战利品清单 → AddToInventory 验证 /
容器持有物枚举 → TransferItem 转移 + GetItemQuantity 数量验证）+ 诚实降级
（item_remove 事件口径）/ probe_loot 只读取证（方法面 + 容器持有物清单）/
probe_blackwall（direct_inject 直传注入 + TweakDB记录枚举+物品注入+玩家施加即移除）/
probe_constructors（表成员枚举+Vector4/EulerAngles多形构造+Teleport 3形态验证）/
player_attack（状态效果主变体+TweakDBID兜底解析+HP注入兜底）/
set_hp_zero / player_move（实机验证过的单一 facility:Teleport 路径，审计修复后移除未验证变体）/
get_round_info / set_scenario（对齐 mock 契约）/ probe_move_apis 注册函数。

用 lupa 执行 init.lua 全文，stub CET 全局（Game / registerForEvent / ObserveAfter / t 等），
测试片段拼接在同一 chunk 末尾以访问 local handlers。
用法: py tests\s5_lua_selftest.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_LUA = ROOT / "game_mods" / "CufBridge" / "init.lua"

STUB = r"""
mode = {variant1_fail = false, async_dispatch = false, dispatch_all_fail = false, teleport_noop = false,
        status_system_missing = false, status_apply_fail = false, status_unverified = false,
        player_teleport_broken = false, t_missing = false, facility_noop = false,
        item_in_target_parts = false, no_loot_list = false, no_item_list = false,
        transfer_fail = false}
hp_store = {}
status_store = {}

local function make_entity(hash, pos)
  local e = {
    _hash = hash,
    _pos = pos or {x = 0, y = 0, z = 0},
  }
  function e:GetEntityID() return {hash = self._hash} end
  function e:GetWorldPosition() return self._pos end
  function e:GetAttitudeAgent() return nil end
  function e:GetClassName() return 'NPC' end
  function e:Teleport(pos)
    if mode.player_teleport_broken then error('stub_player_teleport_broken') end
    if mode.teleport_throws then error('stub_teleport_throws') end
    if mode.teleport_noop then return end
    self._pos = {x = pos.x, y = pos.y, z = pos.z}
  end
  return e
end

player_entity = make_entity(1, {x = 0, y = 0, z = 0})
local npc_entity = make_entity(7, {x = 3, y = 0, z = 0})

local part_npc = {}
function part_npc:GetComponent()
  return {GetEntity = function() return npc_entity end}
end

item_container = make_entity(501, {x = 1, y = 0, z = 0})
item_container.GetClassName = function() return 'LootContainerObjectAnimatedByTransform' end
item_container.GetLootItemsList = function()
  if mode.no_loot_list then return nil end
  return {t('Items.TestLoot')}
end
part_item = {}
function part_item:GetComponent()
  return {GetEntity = function() return item_container end}
end

local statpools = {}
function statpools:GetStatPoolValue(eid, ptype)
  return hp_store[eid.hash] or 0
end
function statpools:RequestSettingStatPoolValue(eid, ptype, value, instigator, notify)
  if mode.dispatch_all_fail then error('stub_dispatch_fail') end
  if mode.variant1_fail and notify == false then error('stub_variant1_fail') end
  if mode.async_dispatch then return true end
  hp_store[eid.hash] = value
  return true
end

Game = {}
Game.GetPlayer = function() return player_entity end
Game.GetTimeSystem = function()
  return {GetGameTime = function() return 0 end}
end
Game.GetStatPoolsSystem = function() return statpools end
local status_system = {}
function status_system:ApplyStatusEffect(eid, tdbid, instigator)
  if mode.status_apply_fail then error('stub_status_apply_fail') end
  if not mode.status_unverified then
    if not status_store[eid.hash] then status_store[eid.hash] = {} end
    status_store[eid.hash][tdbid] = true
  end
  return true
end
function status_system:HasStatusEffect(eid, tdbid)
  local bucket = status_store[eid.hash]
  return bucket ~= nil and bucket[tdbid] == true
end
function status_system:RemoveStatusEffect(eid, tdbid)
  local bucket = status_store[eid.hash]
  if bucket then bucket[tdbid] = nil end
  return true
end
Game.GetStatusEffectSystem = function()
  if mode.status_system_missing then error('stub_no_status_system') end
  return status_system
end
Vector4 = {}
function Vector4:new(a, b, c, d)
  if type(a) == 'table' then
    return {x = a.x, y = a.y, z = a.z, w = a.w, __vec4 = true}
  end
  if type(a) == 'number' then
    return {x = a, y = b, z = c, w = d, __vec4 = true}
  end
  return {x = 0, y = 0, z = 0, w = 0, __vec4 = true}
end
EulerAngles = {}
function EulerAngles:new(a, b, c)
  if type(a) == 'table' then
    return {yaw = a.yaw or 0, pitch = a.pitch or 0, roll = a.roll or 0}
  end
  if type(a) == 'number' then
    return {yaw = a, pitch = b, roll = c}
  end
  return {yaw = 0, pitch = 0, roll = 0}
end
local teleport_facility = {}
function teleport_facility:Teleport(entity, pos, ori)
  if mode.teleport_throws then error('stub_teleport_throws') end
  if mode.teleport_noop or mode.facility_noop then return end
  entity._pos = {x = pos.x, y = pos.y, z = pos.z}
end
Game.GetTeleportationFacility = function() return teleport_facility end
inventory_store = {}
valid_items = {}
Game.AddToInventory = function(id, qty)
  if not valid_items[id] then return false end
  inventory_store[id] = (inventory_store[id] or 0) + (qty or 1)
  return true
end
local transaction_system = {}
function transaction_system:GetItemQuantity(entity, td)
  local name = td
  if type(td) == 'table' and td.__tdbid then name = td.__tdbid end
  return inventory_store[name] or 0
end
owner_items = {}
function transaction_system:GetItemList(entity)
  if mode.no_item_list then return nil end
  if entity == player_entity then
    local list = {}
    for name in pairs(inventory_store) do
      list[#list + 1] = {__tdbid = name}
    end
    return list
  end
  if type(entity) == 'table' and entity._hash ~= nil then
    return owner_items[entity._hash]
  end
  return nil
end
function transaction_system:TransferItem(from_e, to_e, td, qty)
  if mode.transfer_fail then return false end
  local name = td
  if type(td) == 'table' and td.__tdbid then name = td.__tdbid end
  if type(from_e) ~= 'table' or from_e._hash == nil then return false end
  if owner_items[from_e._hash] == nil then return false end
  owner_items[from_e._hash] = nil
  inventory_store[name] = (inventory_store[name] or 0) + 1
  return true
end
function transaction_system:GetMoney(entity) return 0 end
Game.GetTransactionSystem = function() return transaction_system end
tweakdb_records = {}
TweakDB = {}
function TweakDB.GetRecord(key)
  local name = key
  if type(key) == 'table' and key.__tdbid then name = key.__tdbid end
  if tweakdb_records[name] then return {__record = name} end
  return nil
end
local tdbid_cache = {}
t = function(s)
  if mode.t_missing then error('stub_t_missing') end
  if tdbid_cache[s] == nil then tdbid_cache[s] = {__tdbid = s} end
  return tdbid_cache[s]
end
TweakDBID = {}
function TweakDBID:Get(s)
  if tdbid_cache[s] == nil then tdbid_cache[s] = {__tdbid = s} end
  return tdbid_cache[s]
end
TDBID = {}
TDBID.ToString = function(td)
  if type(td) == 'table' and td.__tdbid then return td.__tdbid end
  return tostring(td)
end
Game.GetTargetingSystem = function()
  return {GetTargetParts = function(player, sq)
    if mode.item_in_target_parts then return {part_npc, part_item} end
    return {part_npc}
  end}
end
Game.GetDynamicEntitySystem = function()
  return {GetAllEntities = function(self, handle)
    handle[1] = item_container
  end}
end
Game['TSQ_ALL;'] = function() return {} end
GameTime = {ToFloat = function() return 0.0 end}
gamedataStatPoolType = {Health = 1}
EAIAttitude = {AIA_Hostile = 3}

registerForEvent = function(name, fn) end
ObserveAfter = function(class, fn, cb) end
GetMod = function() return nil end
NameToString = function(s) return tostring(s) end
"""

TESTS = r"""
local results = {}
local function check(name, cond, detail)
  results[#results + 1] = {name = name, pass = cond == true, detail = tostring(detail or '')}
end

hp_store[7] = 100
local r

r = handlers.player_attack({})
check('attack_missing_target_rejected',
  r.accepted == false and r.reason == 'missing_target_id' and type(r.action_id) == 'string',
  r.reason)

r = handlers.player_attack({target_id = 'EID:1'})
check('attack_self_rejected',
  r.accepted == false and r.reason == 'target_is_self', r.reason)

r = handlers.player_attack({target_id = 'EID:999'})
check('attack_not_found_rejected',
  r.accepted == false and string.sub(r.reason, 1, 17) == 'target_not_found:', r.reason)

local function inj_attempts(r)
  local out = {}
  for _, a in ipairs(r.attempts) do
    if a.kind == 'injection' then out[#out + 1] = a end
  end
  return out
end

hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
check('attack_status_effect_applied',
  r.accepted == true and r.reason == 'status_effect_applied' and r.status_effect == 'poison'
  and r.attempts[1].kind == 'status' and r.attempts[1].verified == true
  and r.hp_before == 100 and r.hp_after == 100,
  r.reason .. '/' .. tostring(r.status_effect))

mode.status_apply_fail = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
local inj = inj_attempts(r)
check('attack_status_fail_falls_back_injection',
  r.accepted == true and r.reason == 'damage_applied'
  and r.hp_after == 80 and #inj == 1,
  r.reason .. ' hp=' .. tostring(r.hp_after))
mode.status_apply_fail = false

mode.status_unverified = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
check('attack_status_unverified_still_accepted',
  r.accepted == true and r.reason == 'status_effect_applied'
  and r.status_effect == 'poison' and r.hp_after == 100
  and #r.attempts == 1 and r.attempts[1].verified == false,
  r.reason .. ' attempts=' .. tostring(#r.attempts))
mode.status_unverified = false

mode.status_system_missing = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7', damage = 25})
check('attack_status_system_missing_then_injection',
  r.accepted == true and r.reason == 'damage_applied'
  and r.hp_before == 100 and r.hp_after == 75
  and r.attempts[1].variant == 'system',
  'hp ' .. tostring(r.hp_before) .. '->' .. tostring(r.hp_after))

mode.variant1_fail = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
inj = inj_attempts(r)
check('attack_variant_fallback',
  r.accepted == true and inj[1].ok == false and inj[2].ok == true,
  'v1.ok=' .. tostring(inj[1].ok) .. ' v2.ok=' .. tostring(inj[2].ok))
mode.variant1_fail = false

mode.async_dispatch = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
check('attack_damage_dispatched_async',
  r.accepted == true and r.reason == 'damage_dispatched'
  and r.hp_before == 100 and r.hp_after == 100,
  r.reason)
mode.async_dispatch = false

mode.dispatch_all_fail = true
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
inj = inj_attempts(r)
check('attack_dispatch_failed',
  r.accepted == false and r.reason == 'damage_dispatch_failed'
  and inj[1].ok == false and inj[3].ok == false,
  r.reason)
mode.dispatch_all_fail = false
mode.status_system_missing = false

hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7', variant = 'burning'})
check('attack_variant_burning_forced',
  r.accepted == true and r.status_effect == 'burning'
  and #r.attempts == 1 and r.attempts[1].variant == 'burning'
  and r.variant_forced == 'burning' and r.variant_policy_enabled == true,
  tostring(r.status_effect) .. ' attempts=' .. tostring(#r.attempts))

hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7', variant = 'no_such'})
check('attack_variant_unknown_rejected',
  r.accepted == false and string.sub(r.reason, 1, 15) == 'unknown_variant',
  r.reason)

r = handlers.set_attack_variant_policy({enabled = false})
check('variant_policy_toggle_off', r.ok == true and r.variant_policy_enabled == false,
  tostring(r.variant_policy_enabled))

hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7', variant = 'burning'})
check('attack_variant_rejected_when_policy_off',
  r.accepted == false and r.reason == 'variant_policy_disabled', r.reason)

hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
check('attack_default_chain_intact_when_policy_off',
  r.accepted == true and r.status_effect == 'poison'
  and r.variant_forced == nil and r.variant_policy_enabled == false,
  tostring(r.status_effect))

r = handlers.set_attack_variant_policy({enabled = true})
check('variant_policy_toggle_on', r.ok == true and r.variant_policy_enabled == true,
  tostring(r.variant_policy_enabled))

-- 0.6.16：check_status_effect 延迟验证（只读轮询）
hp_store[7] = 100
r = handlers.player_attack({target_id = 'EID:7'})
r = handlers.check_status_effect({target_id = 'EID:7'})
local poison_present = false
for _, e in ipairs(r.effects or {}) do
  if e.name == 'poison' and e.present == true then poison_present = true end
end
check('check_status_effect_reports_poison',
  r.ok == true and poison_present and r.hp == 100,
  'hp=' .. tostring(r.hp) .. ' poison=' .. tostring(poison_present))

r = handlers.check_status_effect({})
check('check_status_effect_missing_target',
  r.ok == false and r.reason == 'missing_target_id', tostring(r.reason))

r = handlers.check_status_effect({target_id = 'EID:999999'})
check('check_status_effect_target_not_found',
  r.ok == false and string.sub(r.reason or '', 1, 16) == 'target_not_found',
  tostring(r.reason))

hp_store[7] = 100
r = handlers.set_hp_zero({target_id = 'EID:7'})
check('set_hp_zero_zeroes_hp',
  r.accepted == true and r.reason == 'hp_zeroed'
  and r.hp_before == 100 and r.hp_after == 0,
  r.reason)

r = handlers.set_hp_zero({})
check('set_hp_zero_missing_target_rejected',
  r.accepted == false and r.reason == 'missing_target_id', r.reason)

r = handlers.set_hp_zero({target_id = 'EID:1'})
check('set_hp_zero_self_rejected',
  r.accepted == false and r.reason == 'target_is_self', r.reason)

r = handlers.player_move({x = 1})
check('move_missing_xyz_rejected',
  r.accepted == false and r.reason == 'missing_xyz', r.reason)

player_entity._pos = {x = 0, y = 0, z = 0}
r = handlers.player_move({x = 10, y = 5, z = 2})
check('move_teleport_applied',
  r.accepted == true and r.reason == 'teleport_applied'
  and r.end_pos ~= nil and r.end_pos.x == 10,
  'end_x=' .. tostring(r.end_pos and r.end_pos.x))

mode.teleport_noop = true
player_entity._pos = {x = 0, y = 0, z = 0}
r = handlers.player_move({x = 99, y = 0, z = 0})
check('move_teleport_dispatched_when_position_lags',
  r.accepted == true and r.reason == 'teleport_dispatched'
  and r.note == 'position_verification_deferred_to_observer'
  and #r.attempts == 1 and r.attempts[1].variant == 1,
  r.reason .. ' attempts=' .. tostring(#r.attempts))
mode.teleport_noop = false

mode.teleport_throws = true
player_entity._pos = {x = 0, y = 0, z = 0}
r = handlers.player_move({x = 99, y = 0, z = 0})
check('move_teleport_not_effective_when_all_throw',
  r.accepted == false and r.reason == 'teleport_not_effective'
  and #r.attempts == 1 and r.attempts[1].ok == false
  and type(r.attempts[1].err) == 'string',
  r.reason .. ' attempts=' .. tostring(#r.attempts))
mode.teleport_throws = false

mode.player_teleport_broken = true
player_entity._pos = {x = 0, y = 0, z = 0}
r = handlers.player_move({x = 7, y = 3, z = 1})
check('move_single_facility_path_applied',
  r.accepted == true and r.reason == 'teleport_applied'
  and r.end_pos ~= nil and r.end_pos.x == 7
  and #r.attempts == 1 and r.attempts[1].variant == 1,
  'variant=' .. tostring(r.attempts[1] and r.attempts[1].variant) .. ' reason=' .. r.reason)
mode.player_teleport_broken = false

r = handlers.get_round_info()
check('get_round_info_live_mode_fields',
  r.round_id == 0 and r.scenario == 'live' and type(r.pending_events) == 'number',
  'scenario=' .. tostring(r.scenario))

r = handlers.set_scenario({'live'})
check('set_scenario_live_accepted',
  r.scenario == 'live' and r.round_id == 0
  and r.note == 'live_mode_no_preset_scenarios',
  tostring(r.note))

local sok, serr = pcall(function() handlers.set_scenario({'anomaly'}) end)
check('set_scenario_unknown_raises',
  sok == false and type(serr) == 'string' and string.find(serr, 'unknown_scenario') ~= nil,
  tostring(serr))

mode.t_missing = true
r = handlers.player_attack({target_id = 'EID:7'})
check('attack_tdbid_fallback_via_tweakdbid',
  r.accepted == true and r.reason == 'status_effect_applied'
  and r.status_effect == 'poison',
  r.reason)
mode.t_missing = false

r = handlers.probe_move_apis()
check('probe_move_apis_returns_info',
  r.player_available == true and r.player_Teleport_type == 'function',
  'tf=' .. tostring(r.tf_global_type) .. ' teleport=' .. tostring(r.player_Teleport_type))

local a = handlers.player_move({x = 1, y = 1, z = 1})
local b = handlers.player_move({x = 2, y = 1, z = 1})
check('action_ids_increment',
  a.action_id ~= b.action_id and string.sub(a.action_id, 1, 4) == 'act_',
  a.action_id .. ' vs ' .. b.action_id)

tweakdb_records['Items.HauntedCyberdeck_Legendary'] = true
tweakdb_records['BaseStatusEffect.Corrosion'] = true
tweakdb_records['BaseStatusEffect.Burning'] = true
tweakdb_records['BaseStatusEffect.Poison'] = true
valid_items['Items.HauntedCyberdeck_Legendary'] = true
hp_store[1] = 100

r = handlers.probe_blackwall({})
check('blackwall_default_no_apply',
  r.apply_to_player == false and r.player_apply == nil and r.status_records ~= nil
  and #r.status_records == 6,
  'records=' .. tostring(r.status_records and #r.status_records))

local function find_bw_item(res, id)
  for _, e in ipairs(res.items) do
    if e.item == id then return e end
  end
  return nil
end

local bi1 = find_bw_item(r, 'Items.HauntedCyberdeck_Legendary')
check('blackwall_item_added_verified',
  bi1 ~= nil and bi1.record_exists == true and bi1.add_result ~= nil
  and bi1.add_result.added == true and bi1.add_result.qty_after == 1,
  bi1 and tostring(bi1.lookup) or 'missing')

local bi2 = find_bw_item(r, 'Items.HauntedCyberdeck_Crafted')
check('blackwall_item_missing_record_skipped',
  bi2 ~= nil and bi2.record_exists == false and bi2.lookup == 'not_found'
  and bi2.add_result == nil,
  bi2 and tostring(bi2.lookup) or 'missing')

r = handlers.probe_blackwall({apply_to_player = true})
check('blackwall_apply_enumerates',
  r.apply_to_player == true and r.player_apply ~= nil and #r.player_apply == 6,
  'n=' .. tostring(r.player_apply and #r.player_apply))

local function find_bw_apply(res, id)
  for _, e in ipairs(res.player_apply) do
    if e.id == id then return e end
  end
  return nil
end

local bc1 = find_bw_apply(r, 'BaseStatusEffect.Corrosion')
check('blackwall_corrosion_apply_verify_remove',
  bc1 ~= nil and bc1.exists == true and bc1.apply_ok == true and bc1.verified == true
  and bc1.remove_ok == true and bc1.gone_after_remove == true
  and bc1.hp_before == 100 and bc1.hp_after_applied == 100 and bc1.hp_after_remove == 100,
  bc1 and tostring(bc1.verified) .. '/' .. tostring(bc1.gone_after_remove) or 'missing')

local bc2 = find_bw_apply(r, 'BaseStatusEffect.Blackwall')
check('blackwall_absent_record_no_apply',
  bc2 ~= nil and bc2.exists == false and bc2.lookup == 'not_found' and bc2.apply_ok == nil,
  bc2 and tostring(bc2.lookup) or 'missing')

local saved_add = Game.AddToInventory
Game.AddToInventory = nil
r = handlers.probe_blackwall({})
bi1 = find_bw_item(r, 'Items.HauntedCyberdeck_Legendary')
check('blackwall_no_add_api_reported',
  bi1 ~= nil and bi1.add_result ~= nil and bi1.add_result.added == false
  and bi1.add_result.reason == 'no_add_api',
  bi1 and bi1.add_result and tostring(bi1.add_result.reason) or 'missing')
Game.AddToInventory = saved_add

local saved_tdb = TweakDB
TweakDB = nil
r = handlers.probe_blackwall({})
check('blackwall_no_tweakdb_api_reported',
  r.status_records ~= nil and r.status_records[1].exists == false
  and r.status_records[1].lookup == 'no_tweakdb_api',
  r.status_records and tostring(r.status_records[1].lookup) or 'nil')
TweakDB = saved_tdb

player_entity._pos = {x = 0, y = 0, z = 0}
r = handlers.probe_constructors({})
check('probe_constructors_enums_and_ctors',
  r.mod_version ~= nil and string.match(r.mod_version, '^%d+%.%d+%.%d+$') ~= nil
  and r.enum_TweakDBID ~= nil and r.enum_TweakDBID.exists == true
  and r.enum_Vector4 ~= nil and r.enum_Vector4.exists == true
  and r.enum_EulerAngles ~= nil and r.enum_EulerAngles.exists == true
  and #r.v4_ctors == 4 and #r.eu_ctors == 3
  and r.player_pos ~= nil,
  'v4=' .. tostring(#r.v4_ctors) .. ' eu=' .. tostring(#r.eu_ctors))

check('probe_constructors_ctor_forms',
  r.v4_ctors[1].ok == true and r.v4_ctors[2].ok == true and r.v4_ctors[3].ok == true
  and r.v4_ctors[4].ok == false
  and r.eu_ctors[1].ok == true and r.eu_ctors[2].ok == true and r.eu_ctors[3].ok == true,
  'call_tbl_ok=' .. tostring(r.v4_ctors[4].ok))

check('probe_constructors_teleport_moves',
  #r.teleport_attempts == 3 and r.teleport_attempts[1].call_ok == true
  and r.teleport_attempts[1].moved == true,
  'n=' .. tostring(#r.teleport_attempts))

valid_items['Items.Preset_Borg4a_HauntedGun'] = true
r = handlers.probe_blackwall({direct_inject = true})
check('blackwall_direct_inject_flag_echoed',
  r.direct_inject == true, tostring(r.direct_inject))

local bi3 = find_bw_item(r, 'Items.Preset_Borg4a_HauntedGun')
check('blackwall_direct_inject_no_record_still_added',
  bi3 ~= nil and bi3.record_exists == false
  and bi3.add_result ~= nil and bi3.add_result.added == true
  and bi3.add_result.ret == 'true',
  bi3 and bi3.add_result and tostring(bi3.add_result.ret) or 'missing')

local bi4 = find_bw_item(r, 'Items.HauntedCyberdeck_Crafted')
check('blackwall_direct_inject_invalid_item_not_verified',
  bi4 ~= nil and bi4.add_result ~= nil and bi4.add_result.added == false
  and bi4.add_result.reason == 'not_verified' and bi4.add_result.ret == 'false',
  bi4 and bi4.add_result and tostring(bi4.add_result.reason) or 'missing')

r = handlers.probe_blackwall({})
local bi5 = find_bw_item(r, 'Items.Preset_Borg4a_HauntedGun')
check('blackwall_default_still_gated',
  r.direct_inject == false and bi5 ~= nil and bi5.record_exists == false
  and bi5.add_result == nil,
  bi5 and tostring(bi5.record_exists) or 'missing')

mode.item_in_target_parts = true
valid_items['Items.TestLoot'] = true
inventory_store['Items.TestLoot'] = nil
item_container._pos = {x = 1, y = 0, z = 0}
handlers.get_events()
r = handlers.player_pickup({target_id = 'EID:501'})
local evs = handlers.get_events()
local got_acq = false
for i = 1, evs.count do
  if evs.events[i].kind == 'item_acquired' then got_acq = true end
end
check('pickup_true_acquisition_verified',
  r.accepted == true and r.reason == 'item_acquired' and r.semantics == 'acquired'
  and r.verified_count == 1 and got_acq
  and inventory_store['Items.TestLoot'] == 1
  and item_container._pos.z == -2000,
  tostring(r.reason))

mode.no_loot_list = true
item_container._pos = {x = 1, y = 0, z = 0}
handlers.get_events()
r = handlers.player_pickup({target_id = 'EID:501'})
evs = handlers.get_events()
local got_rem = false
for i = 1, evs.count do
  if evs.events[i].kind == 'item_remove' then got_rem = true end
end
check('pickup_fallback_remove_no_loot_list',
  r.accepted == true and r.reason == 'item_removed' and r.semantics == 'item_remove'
  and got_rem and item_container._pos.z == -2000,
  tostring(r.reason))

mode.no_loot_list = false
valid_items['Items.TestLoot'] = nil
inventory_store['Items.TestLoot'] = nil
item_container._pos = {x = 1, y = 0, z = 0}
handlers.get_events()
r = handlers.player_pickup({target_id = 'EID:501'})
evs = handlers.get_events()
got_rem = false
for i = 1, evs.count do
  if evs.events[i].kind == 'item_remove' then got_rem = true end
end
check('pickup_unverified_loot_falls_back',
  r.accepted == true and r.reason == 'item_removed' and got_rem
  and inventory_store['Items.TestLoot'] == nil,
  tostring(r.reason))

mode.no_loot_list = true
mode.teleport_throws = true
r = handlers.player_pickup({target_id = 'EID:501'})
check('pickup_all_remove_fail_rejected',
  r.accepted == false and r.reason == 'item_remove_failed',
  tostring(r.reason))
mode.teleport_throws = false
mode.no_loot_list = false

r = handlers.player_pickup({target_id = 'EID:999'})
check('pickup_target_not_found_rejected',
  r.accepted == false and r.reason ~= nil and r.reason:find('target_not_found') == 1,
  tostring(r.reason))

r = handlers.player_pickup({target_id = 'EID:7'})
check('pickup_not_item_rejected',
  r.accepted == false and r.reason ~= nil and r.reason:find('target_not_item') == 1,
  tostring(r.reason))

item_container._pos = {x = 1, y = 0, z = 0}
local cnt_before = 0
for _ in pairs(inventory_store) do cnt_before = cnt_before + 1 end
r = handlers.probe_loot({target_id = 'EID:501'})
local cnt_after = 0
for _ in pairs(inventory_store) do cnt_after = cnt_after + 1 end
check('probe_loot_forensics_no_mutation',
  r.target_found == true and r.loot_list_readable == true and r.loot_count == 1
  and r.loot_names[1] == 'Items.TestLoot'
  and r.entity_methods['GetLootItemsList'] == 'function'
  and r.tsys_available == true and r.add_to_inventory_type == 'function'
  and r.tdbid_tostring_type == 'function'
  and cnt_before == cnt_after
  and r.snapshot_before.item_count == r.snapshot_after.item_count,
  tostring(r.loot_count))

r = handlers.probe_loot({})
check('probe_loot_nearest_item_fallback',
  r.target_found == true and r.nearest_item_id == 'EID:501',
  tostring(r.nearest_item_id))

r = handlers.probe_loot({target_id = 'EID:999'})
check('probe_loot_resolve_failed_falls_back_nearest',
  r.resolve_failed == true and r.target_found == true,
  tostring(r.resolve_failed))

mode.no_loot_list = true
mode.transfer_fail = false
owner_items[501] = {t('Items.DropLoot')}
inventory_store['Items.DropLoot'] = nil
item_container._pos = {x = 1, y = 0, z = 0}
handlers.get_events()
r = handlers.player_pickup({target_id = 'EID:501'})
evs = handlers.get_events()
got_acq = false
for i = 1, evs.count do
  if evs.events[i].kind == 'item_acquired' then got_acq = true end
end
check('pickup_container_transfer_verified',
  r.accepted == true and r.reason == 'item_acquired' and r.semantics == 'acquired'
  and r.loot_method == 'container_transfer' and r.verified_count == 1
  and got_acq and inventory_store['Items.DropLoot'] == 1
  and owner_items[501] == nil and item_container._pos.z == -2000,
  tostring(r.reason))

owner_items[501] = {t('Items.DropLoot')}
item_container._pos = {x = 1, y = 0, z = 0}
r = handlers.probe_loot({target_id = 'EID:501'})
check('probe_loot_container_owner_list',
  r.container_item_list_readable == true and r.container_item_count == 1
  and r.container_item_names[1] == 'Items.DropLoot',
  tostring(r.container_item_count))

mode.transfer_fail = true
valid_items['Items.DropLoot'] = nil
inventory_store['Items.DropLoot'] = nil
item_container._pos = {x = 1, y = 0, z = 0}
handlers.get_events()
r = handlers.player_pickup({target_id = 'EID:501'})
evs = handlers.get_events()
got_rem = false
for i = 1, evs.count do
  if evs.events[i].kind == 'item_remove' then got_rem = true end
end
check('pickup_transfer_fail_falls_back',
  r.accepted == true and r.reason == 'item_removed' and got_rem
  and r.transfer ~= nil and r.transfer.verified == 0
  and inventory_store['Items.DropLoot'] == nil,
  tostring(r.reason))
mode.transfer_fail = false
mode.no_loot_list = false

inventory_store['Items.Preset_Saratoga'] = 1
r = handlers.probe_inventory_check(
  {candidates = {'Items.Preset_Saratoga', 'Items.DoesNotExist'}})
check('probe_inventory_check_quantities',
  r.player_available == true and r.tsys_available == true
  and r.results[1].tdbid_ok == true and r.results[1].qty == 1
  and r.results[1].variants[1].form == 'player_td'
  and r.results[1].variants[1].ok == true
  and r.results[1].variants[1].qty == 1
  and #r.results[1].variants == 3
  and r.results[2].tdbid_ok == true and r.results[2].qty == 0,
  tostring(r.results[1].qty))
inventory_store['Items.Preset_Saratoga'] = nil

for k in pairs(inventory_store) do inventory_store[k] = nil end
inventory_store['Items.Preset_Saratoga'] = 1
r = handlers.probe_inventory_shape({})
check('probe_inventory_shape_table_form',
  r.call_ok == true and r.ret_type == 'table' and r.count == 1
  and r.elem_probes[1].etype == 'table'
  and r.elem_probes[1].qty_ok == true and r.elem_probes[1].qty == 1,
  tostring(r.ret_type) .. '/' .. tostring(r.count))

r = handlers.probe_ground_apis()
check('probe_ground_apis_smoke',
  r.mod_version ~= nil and string.match(r.mod_version, '^%d+%.%d+%.%d+$') ~= nil
  and type(r.candidates) == 'table'
  and type(r.globals) == 'table' and type(r.vector) == 'table',
  tostring(r.mod_version))

r = handlers.probe_ground_apis_v2()
check('probe_ground_apis_v2_smoke',
  r.mod_version ~= nil and string.match(r.mod_version, '^%d+%.%d+%.%d+$') ~= nil
  and type(r.sqs) == 'table'
  and type(r.trace) == 'table' and type(r.enum_keys) == 'table',
  tostring(r.mod_version))

return results
"""


def main() -> int:
    try:
        from lupa import LuaRuntime
    except ImportError:
        print("SKIP: lupa not installed")
        return 2

    src = INIT_LUA.read_text(encoding="utf-8")
    lua = LuaRuntime()
    lua.execute(STUB)
    results = lua.execute(src + "\n" + TESTS)

    total = len(results)
    passed = 0
    for i in range(1, total + 1):
        rec = results[i]
        name = rec["name"]
        ok = rec["pass"]
        detail = rec["detail"]
        if ok:
            passed += 1
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" | " + detail if detail else "")))

    print("\n%d/%d passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
