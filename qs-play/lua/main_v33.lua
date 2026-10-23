--[[
SBBridge v3.1 for Stellar Blade (UE4SS 3.0.1) — 只读为主
================================================================
v3.1 = v3 + 一次性探针（全部只读，按需触发，不进 tick 常驻）：
  probe player   → probe_player.json   玩家属性 / 组件类名 / 组件上的候选属性
  probe enemy    → probe_enemy.json    最近 CH_M_* 的同上
  probe widgets  → probe_widgets.json  全部 UMG ProgressBar 的 Percent/可见性
  probe ai       → probe_ai.json       最近敌人的控制器 + 黑板键（锁定/攻击状态）
  probe          → probe.json          最近 Pawn（原 v1/v2 行为）

v3 的三件：
1. **镜头朝向** `view`：读 SBNetworkPlayerController.ControlRotation。
   v2 的 player.yaw 是身体朝向，站着转镜头时不动——不能当镜头用。
2. **速度** `vel`：GetVelocity 的 2D 速度。
3. **敌人类名泛化**：类名包含 CH_M_ 的最近一个（不写死 CH_M_NA_29）。
   另外输出 enemy.yaw 与 facing_player（±45° 内算面向玩家）。

传输仍是文件 IPC：每 80ms 写 state.json；贵操作每 5 拍刷一次。
⚠ 日志里的"tick 耗时"用 os.clock()，那是**进程 CPU 时间**，多线程游戏下
   会虚高（实测 50ms 而实际间隔 87~106ms）。判成本只看 state.json 的实际间隔。

安全边界：默认只读；probe* 只读不需解锁；移动类命令必须存在 allow_write.txt。
]]
local MOD = "SBBridge"
local DIR = "D:/SteamLibrary/steamapps/common/StellarBlade/SB/Binaries/Win64/ue4ss/Mods/SBBridge/"
local STATE = DIR .. "state.json"
local CMD = DIR .. "cmd.json"
local UNLOCK = DIR .. "allow_write.txt"
local PROBE = DIR .. "probe.json"
local TICK_MS = 80
local REFRESH_EVERY = 5

local function log(msg) print(string.format("[%s] %s\n", MOD, msg)) end

local function file_exists(p)
    local f = io.open(p, "r")
    if f then f:close() return true end
    return false
end

local function write_file(p, s)
    local f = io.open(p, "w")
    if not f then return false end
    f:write(s)
    f:close()
    return true
end

-- 换关卡/GC 后，缓存里的 UObject 会变成"nullptr 但仍非 nil"的包装体；
-- 直接调成员函数会抛 native 错误，pcall 也不一定兜住（v3.1 实测把
-- LoopAsync 线程打死了）。所以每次用之前先探一下名字。
local function valid(obj)
    if obj == nil then return false end
    -- UE4SS 提供 IsValid(obj)；先用它，避免对已销毁对象调成员函数
    -- 触发无法被 pcall 兜住的 native 错误。
    local ok, v = pcall(function() return IsValid(obj) end)
    if ok and type(v) == "boolean" then return v end
    local ok2, v2 = pcall(function() return obj:IsValid() end)
    if ok2 and type(v2) == "boolean" then return v2 end
    local ok3 = pcall(function() return obj:GetFullName() end)
    return ok3
end

local function safe_name(obj)
    if not valid(obj) then return "<invalid>" end
    local ok, n = pcall(function() return obj:GetFullName() end)
    return ok and tostring(n) or "<invalid>"
end

local function safe_class(obj)
    if not valid(obj) then return "<invalid>" end
    local ok, c = pcall(function() return obj:GetClass():GetFullName() end)
    return ok and tostring(c) or "<invalid>"
end

local function get_loc(obj)
    if not valid(obj) then return nil end
    local ok, v = pcall(function() return obj:K2_GetActorLocation() end)
    if ok and v then return v.X, v.Y, v.Z end
    return nil
end

local function get_rot(obj)
    if not valid(obj) then return nil end
    local ok, r = pcall(function() return obj:K2_GetActorRotation() end)
    if ok and r then return r.Pitch, r.Yaw, r.Roll end
    return nil
end

-- 镜头朝向：优先属性，其次 GetControlRotation()。
-- 这是 v3 的核心新增；没有它就无法把"look(N)"换算成"转了多少度"。
local function get_ctl_rot(ctl)
    if not valid(ctl) then return nil end
    local ok, r = pcall(function() return ctl.ControlRotation end)
    if ok and r and r.Yaw then return r.Pitch, r.Yaw, r.Roll end
    local ok2, r2 = pcall(function() return ctl:GetControlRotation() end)
    if ok2 and r2 and r2.Yaw then return r2.Pitch, r2.Yaw, r2.Roll end
    return nil
end

local function get_speed2d(obj)
    if not valid(obj) then return nil end
    local ok, v = pcall(function() return obj:GetVelocity() end)
    if ok and v then
        return math.sqrt(v.X * v.X + v.Y * v.Y), v.Z
    end
    return nil
end

local function dist(a, b)
    local dx, dy, dz = a[1] - b[1], a[2] - b[2], a[3] - b[3]
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

-- UE4SS 对不存在的属性会返回非 nil 包装对象，拿 v ~= nil 会误报。
local function safe_val(v)
    if v == nil then return nil end
    local t = type(v)
    if t == "number" then return string.format("%.4f", v) end
    if t == "boolean" then return tostring(v) end
    if t == "string" then
        if v == "nil" or v == "" or v == "None" then return nil end
        return v
    end
    if t == "userdata" or t == "table" then
        local ok, name = pcall(function() return v:GetFullName() end)
        if ok and name and tostring(name) ~= "nil" and tostring(name) ~= "None" then
            return "<obj>" .. tostring(name)
        end
        return nil
    end
    return nil
end

local function read_cmd()
    local f = io.open(CMD, "r")
    if not f then return nil end
    local s = f:read("*a") or ""
    f:close()
    os.remove(CMD)
    local action = s:match('"action"%s*:%s*"([%w_%-]+)"')
    local num = tonumber(s:match('"arg"%s*:%s*([%-%d%.]+)'))
    local str = s:match('"arg"%s*:%s*"([%w_%-]+)"')
    return action, num or str
end

-- ── v3.1 属性探针的候选名 ──────────────────────────────────
-- 只读候选，不猜值：读出什么算什么，读不到就如实空着。
-- 目的：解决"玩家/敌人 HP、护盾到底挂在哪个对象/组件上"。
local ATTR_CANDIDATES = {
    "HP", "Hp", "hp", "Health", "MaxHealth", "CurrentHealth", "MaxHP",
    "CurrentHP", "HitPoint", "Damage", "Shield", "ShieldHP", "MaxShield",
    "CurrentShield", "ShieldPoint", "Guard", "Stamina", "MaxStamina",
    "Energy", "BE", "BU", "IsDead", "bDead", "bIsDead", "IsInvincible",
    "bInvincible", "Stat", "Stats", "Status", "AttributeSet",
    "AbilitySystemComponent", "HealthComponent", "CombatComponent",
    "StatComponent", "StatusComponent", "CharacterMovement", "Controller",
    "AIController", "PlayerState", "Pawn",
}

local function props_of(obj, maxn)
    local out = {}
    for _, name in ipairs(ATTR_CANDIDATES) do
        local ok, v = pcall(function() return obj[name] end)
        if ok then
            local val = safe_val(v)
            if val then
                out[#out + 1] = string.format('%q:%q', name, val)
                if #out >= maxn then break end
            end
        end
    end
    return "{" .. table.concat(out, ",") .. "}"
end

-- 按 Owner 过滤组件。用 GetFullName 比较而不是 ==：UE4SS 的包装对象
-- 不保证同一个 Lua userdata，直接 == 可能永远 false。
local function components_of(obj, maxn)
    local full = safe_name(obj)
    local parts, n = {}, 0
    for _, c in ipairs(FindAllOf("ActorComponent") or {}) do
        local ok, owner = pcall(function() return c:GetOwner() end)
        if ok and owner then
            local okn, on = pcall(function() return owner:GetFullName() end)
            if okn and on == full then
                local cls = "<unknown>"
                local okc, cname = pcall(function() return c:GetClass():GetFullName() end)
                if okc and cname then cls = tostring(cname) end
                n = n + 1
                parts[#parts + 1] = string.format(
                    '{"class":%q,"props":%s}', cls, props_of(c, 12))
                if n >= maxn then break end
            end
        end
    end
    return n, "[" .. table.concat(parts, ",") .. "]"
end

local function write_probe(name, fields)
    return write_file(DIR .. name, "{" .. table.concat(fields, ",") .. "}")
end

local function nearest_monster(player)
    if not player then return nil end
    local px, py, pz = get_loc(player)
    if not px then return nil end
    local best, bestD
    for _, a in ipairs(FindAllOf("Pawn") or {}) do
        local x, y, z = get_loc(a)
        if x then
            local okc, c = pcall(function() return a:GetClass():GetFullName() end)
            if okc and c and tostring(c):find("CH_M_", 1, true) then
                local d = dist({ x, y, z }, { px, py, pz })
                if not bestD or d < bestD then best, bestD = a, d end
            end
        end
    end
    return best, bestD
end

local function probe_obj(label, obj, dist_cm, out_name)
    if not valid(obj) then return "probe " .. label .. ": 找不到对象" end
    local n, comps = components_of(obj, 80)
    local fields = {
        string.format('"label":%q', label),
        string.format('"full_name":%q', safe_name(obj)),
        string.format('"class":%q', safe_class(obj)),
        string.format('"dist_cm":%s', dist_cm and string.format("%.1f", dist_cm) or "null"),
        '"props":' .. props_of(obj, 40),
        string.format('"component_count":%d', n),
        '"components":' .. comps,
    }
    write_probe(out_name, fields)
    log(string.format("probe %s: %d 个组件，写出 %s", label, n, out_name))
    return "probe " .. label .. ": ok (" .. n .. " components)"
end

-- ── 只读探针 ────────────────────────────────────────────────
local PROBE_CANDIDATES = {
    "HP", "Hp", "Health", "CurrentHP", "MaxHP", "HitPoint", "Damage",
    "Shield", "ShieldHP", "Guard", "Stamina", "Groggy", "Stagger", "Break",
    "IsDead", "bDead", "bIsDead", "Velocity", "Mesh", "CharacterMovement",
    "Controller", "AIController", "Owner", "Tags",
}
local CTL_CANDIDATES = { "Blackboard", "BrainComponent", "PathFollowingComponent",
                         "FocusActor", "Pawn", "Character", "MoveTarget" }

local function probe_nearest()
    local player = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    local px, py, pz = get_loc(player)
    if not px then return "probe: no player" end
    local all = FindAllOf("Pawn")
    if not all then return "probe: FindAllOf(Pawn) 空" end
    local best, bestD
    for _, a in ipairs(all) do
        local x, y, z = get_loc(a)
        if x and not (x == px and y == py and z == pz) then
            local d = dist({ x, y, z }, { px, py, pz })
            if not bestD or d < bestD then best, bestD = a, d end
        end
    end
    if not best then return "probe: 附近没有别的 Pawn" end
    local out = {
        string.format('"full_name":%q', safe_name(best)),
        string.format('"class":%q', safe_class(best)),
        string.format('"dist_cm":%.1f', bestD),
        string.format('"pawn_count":%d', #all),
    }
    local props = {}
    for _, name in ipairs(PROBE_CANDIDATES) do
        local ok, v = pcall(function() return best[name] end)
        if ok then
            local val = safe_val(v)
            if val then props[#props + 1] = string.format('%q:%q', name, val) end
        end
    end
    out[#out + 1] = '"readable_props":{' .. table.concat(props, ",") .. "}"
    local okc, ctl = pcall(function() return best["Controller"] end)
    if okc and safe_val(ctl) then
        local cprops = {}
        for _, name in ipairs(CTL_CANDIDATES) do
            local ok, v = pcall(function() return ctl[name] end)
            if ok then
                local val = safe_val(v)
                if val then cprops[#cprops + 1] = string.format('%q:%q', name, val) end
            end
        end
        out[#out + 1] = '"controller_props":{' .. table.concat(cprops, ",") .. "}"
    end
    write_file(PROBE, "{" .. table.concat(out, ",") .. "}")
    log(string.format("probe: %d Pawn，最近 %s @%.0fcm", #all, safe_name(best), bestD))
    return "probe: ok"
end

local function probe_player()
    local p = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    return probe_obj("player", p, 0, "probe_player.json")
end

local function probe_enemy()
    local p = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    local e, d = nearest_monster(p)
    return probe_obj("enemy", e, d, "probe_enemy.json")
end

local function probe_widgets()
    local bars, n = {}, 0
    for _, w in ipairs(FindAllOf("ProgressBar") or {}) do
        local okn, nm = pcall(function() return w:GetFullName() end)
        if okn and nm then
            local pct = "null"
            local okp, v = pcall(function() return w.Percent end)
            if okp and type(v) == "number" then pct = string.format("%.4f", v) end
            local vis = "null"
            local okv, b = pcall(function() return w:IsVisible() end)
            if okv and type(b) == "boolean" then vis = tostring(b) end
            n = n + 1
            bars[#bars + 1] = string.format(
                '{"name":%q,"percent":%s,"visible":%s}', tostring(nm), pct, vis)
            if n >= 120 then break end
        end
    end
    write_probe("probe_widgets.json",
        { string.format('"count":%d', n), '"bars":[' .. table.concat(bars, ",") .. "]" })
    log("probe widgets: " .. n .. " 个 ProgressBar")
    return "probe widgets: " .. n
end

-- 黑板的取值接口对 bool/float/object 是三个不同函数，逐个 pcall 试，
-- 哪个成功记哪个；全失败就不写这个键（不把 nil 当 false）。
local function bb_value(bb, key)
    local out = {}
    local oko, vo = pcall(function() return bb:GetValueAsObject(key) end)
    if oko and vo ~= nil then
        out[#out + 1] = '"obj":' .. string.format("%q", safe_val(vo) or "nil")
    end
    local okb, vb = pcall(function() return bb:GetValueAsBool(key) end)
    if okb and type(vb) == "boolean" then
        out[#out + 1] = '"bool":' .. tostring(vb)
    end
    local okf, vf = pcall(function() return bb:GetValueAsFloat(key) end)
    if okf and type(vf) == "number" then
        out[#out + 1] = string.format('"float":%.3f', vf)
    end
    if #out == 0 then return nil end
    return string.format('{%s}', table.concat(out, ","))
end

-- ── 动作/蒙太奇探针（v3.3）─────────────────────────────────
-- 目的：区分「敌人正在起手」和「敌人在挨打」——格挡/闪避的时机必须由
-- 敌人当前播放的动作决定，而不是靠屏幕红度或画面亮度猜。
-- 全部只读；读不到就如实写 null，不编。
local ANIM_PROPS = {
    "CurrentActiveMontage", "ActiveMontages", "MontageInstances",
    "AnimScriptInstance", "bIsPlayingMontage", "CurrentState", "CurrentStateName",
    "bIsAttacking", "AttackState", "bIsGuard", "bIsDodging", "bInvincible",
}
local BP_KEYWORDS = {
    "attack", "state", "montage", "anim", "guard", "parry", "dodge", "invinc",
    "phase", "pattern", "combo", "skill", "groggy", "stagger", "balance",
    "shield", "hp", "health", "damage", "cooldown", "cooltime", "hit",
}

local function cur_montage(anim)
    if not valid(anim) then return nil end
    local m = nil
    local ok, v = pcall(function() return anim:GetCurrentActiveMontage() end)
    if ok and v then m = v end
    if not m then
        local ok2, v2 = pcall(function() return anim.CurrentActiveMontage end)
        if ok2 then m = v2 end
    end
    if not m then return nil end
    local okn, n = pcall(function() return m:GetFullName() end)
    if okn and n and tostring(n) ~= "None" and tostring(n) ~= "nil" then
        return tostring(n)
    end
    return nil
end

-- ⚠ 2026-10-24 删掉了 GetCurrentStateName(0)：没有状态机时按索引取状态会
-- 直接崩（native 崩溃，pcall 兜不住），实测连崩三次。只保留读蒙太奇指针。

-- 按关键字列出类上的属性名（只看名字，不读值）：先给出「有哪些可读的」，
-- 再由下一轮 probe 精确读值。BP 生成类可能没有 PropertyLink，读空也照写。
local function prop_names_filtered(obj, limit)
    local names = {}
    local okc, cls = pcall(function() return obj:GetClass() end)
    if not okc or not cls then return names end
    pcall(function()
        cls:ForEachProperty(function(prop)
            if #names >= limit then return end
            local okn, nm = pcall(function() return prop:GetName() end)
            if okn and nm then
                local low = string.lower(tostring(nm))
                for _, kw in ipairs(BP_KEYWORDS) do
                    if string.find(low, kw, 1, true) then
                        names[#names + 1] = tostring(nm)
                        return
                    end
                end
            end
        end)
    end)
    return names
end

local function anim_props_of(anim)
    local out = {}
    if not valid(anim) then return out end
    for _, name in ipairs(ANIM_PROPS) do
        local ok, v = pcall(function() return anim[name] end)
        if ok then
            local val = safe_val(v)
            if val then out[#out + 1] = string.format('%q:%q', name, val) end
        end
    end
    return out
end

local function montage_list(anim)
    local out = {}
    if not valid(anim) then return out end
    for _, field in ipairs({ "ActiveMontages", "MontageInstances" }) do
        local ok, arr = pcall(function() return anim[field] end)
        if ok and type(arr) == "table" then
            local n = 0
            for _, item in pairs(arr) do
                local okn, nm = pcall(function() return item:GetFullName() end)
                if okn and nm then
                    out[#out + 1] = string.format("%q", tostring(nm))
                    n = n + 1
                    if n >= 8 then break end
                end
            end
        end
    end
    return out
end

local function get_anim_of(actor)
    if not valid(actor) then return nil, nil end
    local okm, mesh = pcall(function() return actor.Mesh end)
    if not (okm and valid(mesh)) then return nil, nil end
    local anim = nil
    local oka, a1 = pcall(function() return mesh:GetAnimInstance() end)
    if oka and valid(a1) then anim = a1 end
    if not anim then
        local okb, a2 = pcall(function() return mesh.AnimScriptInstance end)
        if okb and valid(a2) then anim = a2 end
    end
    return mesh, anim
end

local function probe_anim()
    local p = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    local e, d = nearest_monster(p)
    if not e then return "probe anim: 附近没有 CH_M_*" end
    local mesh, anim = get_anim_of(e)
    local pmesh, panim = get_anim_of(p)
    local anim_names = prop_names_filtered(anim, 60)
    local bp_names = prop_names_filtered(e, 80)
    local kv = {}
    for _, name in ipairs(anim_names) do kv[#kv + 1] = string.format("%q", name) end
    local bk = {}
    for _, name in ipairs(bp_names) do bk[#bk + 1] = string.format("%q", name) end
    local fields = {
        '"probe":"anim"',
        string.format('"enemy":%q,"dist_cm":%.1f', safe_name(e), d or 0),
        '"mesh_class":' .. (valid(mesh) and string.format("%q", safe_class(mesh)) or "null"),
        '"anim_class":' .. (valid(anim) and string.format("%q", safe_class(anim)) or "null"),
        '"montage":' .. (cur_montage(anim) and string.format("%q", cur_montage(anim)) or "null"),
        '"montages":[' .. table.concat(montage_list(anim), ",") .. "]",
        '"anim_props":{' .. table.concat(anim_props_of(anim), ",") .. "}",
        '"anim_prop_names":[' .. table.concat(kv, ",") .. "]",
        '"bp_prop_names":[' .. table.concat(bk, ",") .. "]",
        string.format('"player_montage":%s', cur_montage(panim) and string.format("%q", cur_montage(panim)) or "null"),
        '"player_anim_props":{' .. table.concat(anim_props_of(panim), ",") .. "}",
    }
    write_probe("probe_anim.json", fields)
    log("probe anim: montage=" .. tostring(cur_montage(anim)))
    return "probe anim: ok"
end

local function probe_ai()
    local p = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    local e, d = nearest_monster(p)
    if not e then return "probe ai: 附近没有 CH_M_*" end
    local ctl = nil
    local okc, c = pcall(function() return e.Controller end)
    if okc and safe_val(c) then ctl = c end
    local fields = {
        '"enemy":{' .. string.format('"name":%q,"class":%q,"dist_cm":%.1f',
            safe_name(e), safe_class(e), d or 0) .. "}",
        '"controller_class":' .. (valid(ctl) and string.format("%q", safe_class(ctl)) or "null"),
        '"controller_props":' .. (valid(ctl) and props_of(ctl, 30) or "{}"),
    }
    if ctl then
        local okb, bb = pcall(function() return ctl.Blackboard end)
        if okb and safe_val(bb) then
            local keys = { "TargetActor", "Target", "AttackTarget", "IsAttacking",
                           "bIsAttacking", "AttackState", "State", "ActionState",
                           "MoveTarget", "FocusActor", "HomeLocation" }
            local kv = {}
            for _, k in ipairs(keys) do
                local v = bb_value(bb, k)
                if v then kv[#kv + 1] = string.format('%q:%s', k, v) end
            end
            fields[#fields + 1] = '"blackboard":{' .. table.concat(kv, ",") .. "}"
        else
            fields[#fields + 1] = '"blackboard":null'
        end
    end
    write_probe("probe_ai.json", fields)
    log("probe ai: " .. (ctl and "有控制器" or "无控制器"))
    return "probe ai: ok"
end

-- ── 动作（默认被闸）─────────────────────────────────────────
local allowed = { move_forward = true, move_back = true, move_left = true,
                  move_right = true, stop = true }
local readonly_cmds = { probe = true, list = true }

local function exec(action, arg)
    if readonly_cmds[action] then
        if action == "probe" then
            if arg == "player" then return probe_player() end
            if arg == "enemy" then return probe_enemy() end
            if arg == "widgets" then return probe_widgets() end
            if arg == "ai" then return probe_ai() end
            if arg == "anim" then return probe_anim() end
            return probe_nearest()
        end
        return "list: 未实现"
    end
    if not file_exists(UNLOCK) then return "denied: allow_write.txt 不存在" end
    if not allowed[action] then return "denied: 非白名单 " .. tostring(action) end
    local pc = FindFirstOf("SBNetworkPlayerController")
    local pawn = pc and pc.Pawn
    if not pawn then return "no_pawn" end
    local v = tonumber(arg) or 1.0
    local ok, err = pcall(function()
        if action == "stop" then
            pawn:StopMovement()
        elseif action == "move_forward" then
            pawn:AddMovementInput(pawn:GetActorForwardVector(), v, false)
        elseif action == "move_back" then
            pawn:AddMovementInput(pawn:GetActorForwardVector(), -v, false)
        elseif action == "move_left" then
            pawn:AddMovementInput(pawn:GetActorRightVector(), -v, false)
        elseif action == "move_right" then
            pawn:AddMovementInput(pawn:GetActorRightVector(), v, false)
        end
    end)
    return ok and ("ok:" .. action) or ("error:" .. tostring(err))
end

-- ── 缓存与主循环 ────────────────────────────────────────────
local C = { player = nil, ctl = nil, comp = nil, enemy = nil, roster = "[]" }

local function refresh()
    -- 每次刷新都重新取玩家与控制器：换场景/重生后旧对象会失效。
    C.player = FindFirstOf("CH_P_EVE_01_Blueprint_C")
    C.ctl = FindFirstOf("SBNetworkPlayerController")
    if not valid(C.player) then C.player = nil end
    if not valid(C.ctl) then C.ctl = nil end
    local px, py, pz = get_loc(C.player)
    if not px then return end
    local ploc = { px, py, pz }

    C.comp, C.enemy = nil, nil
    local arr = {}
    for _, a in ipairs(FindAllOf("Pawn") or {}) do
        local x, y, z = get_loc(a)
        if x then
            local d = dist({ x, y, z }, ploc)
            local cls = ""
            local okc, c = pcall(function() return a:GetClass():GetFullName() end)
            if okc and c then cls = tostring(c) end
            if cls:find("CH_Drone_BP", 1, true) and (not C.comp or d < C.comp.d) then
                C.comp = { d = d, name = safe_name(a) }
            end
            if cls:find("CH_M_", 1, true) and (not C.enemy or d < C.enemy.d) then
                local _, eyaw = get_rot(a)
                C.enemy = { d = d, x = x, y = y, z = z, name = safe_name(a),
                            cls = cls, yaw = eyaw or 0, actor = a }
            end
            if d > 30 then arr[#arr + 1] = { d = d, a = a } end
        end
    end
    table.sort(arr, function(p, q) return p.d < q.d end)
    local parts = {}
    for i = 1, math.min(3, #arr) do
        local it = arr[i]
        parts[#parts + 1] = string.format(
            '{"dist_cm":%.1f,"class":%q,"name":%q}',
            it.d, safe_class(it.a), safe_name(it.a))
    end
    C.roster = "[" .. table.concat(parts, ",") .. "]"

    -- v3.3：缓存敌我 AnimInstance，tick 里只做一次 GetCurrentActiveMontage
    C.anim, C.panim = nil, nil
    if C.enemy and valid(C.enemy.actor) then
        local _, anim = get_anim_of(C.enemy.actor)
        if valid(anim) then C.anim = anim end
    end
    if valid(C.player) then
        local _, panim = get_anim_of(C.player)
        if valid(panim) then C.panim = panim end
    end
end

local tickNo, slowWarned = 0, 0
local function tick()
    local t0 = os.clock()
    tickNo = tickNo + 1
    if tickNo == 1 or tickNo % REFRESH_EVERY == 0 then
        local ok, err = pcall(refresh)
        if not ok then log("refresh 失败: " .. tostring(err)) end
    end
    -- 缓存对象在换关卡后可能失效：每拍先验一遍，失效就立刻重取，
    -- 不能拿旧指针去调成员函数（v3.1 就是这么把循环打死的）。
    if not valid(C.player) then C.player = FindFirstOf("CH_P_EVE_01_Blueprint_C") end
    if not valid(C.ctl) then C.ctl = FindFirstOf("SBNetworkPlayerController") end
    local out = {}
    local px, py, pz = get_loc(C.player)
    if px then
        local pitch, yaw, roll = get_rot(C.player)
        out[#out + 1] = string.format(
            '"player":{"x":%.1f,"y":%.1f,"z":%.1f,"yaw":%.2f,"pitch":%.2f}',
            px, py, pz, yaw or 0, pitch or 0)
        local sp, vz = get_speed2d(C.player)
        if sp then
            out[#out + 1] = string.format('"vel":{"speed":%.0f,"vz":%.0f}', sp, vz or 0)
        end
    end
    -- 镜头朝向（v3 新增）：身体 yaw 站着不动，这个才会随鼠标变。
    local cpitch, cyaw = get_ctl_rot(C.ctl)
    if cyaw then
        out[#out + 1] = string.format('"view":{"yaw":%.2f,"pitch":%.2f}',
                                      cyaw, cpitch or 0)
    end
    if C.comp then
        out[#out + 1] = string.format(
            '"companion":{"dist_cm":%.1f,"name":%q}', C.comp.d, C.comp.name)
    end
    if C.enemy then
        local facing = "null"
        if px then
            local dx, dy = px - C.enemy.x, py - C.enemy.y
            local to_p = math.deg(math.atan(dy, dx))
            local diff = ((to_p - C.enemy.yaw + 540) % 360) - 180
            facing = tostring(math.abs(diff) <= 45)
        end
        out[#out + 1] = string.format(
            '"enemy":{"dist_cm":%.1f,"name":%q,"class":%q,"x":%.1f,"y":%.1f,"z":%.1f,"yaw":%.1f,"facing_player":%s,"montage":%s}',
            C.enemy.d, C.enemy.name, C.enemy.cls, C.enemy.x, C.enemy.y,
            C.enemy.z, C.enemy.yaw, facing,
            cur_montage(C.anim) and string.format("%q", cur_montage(C.anim)) or "null")
    end
    -- 玩家自己的蒙太奇：判断「我这一刀还没收完」比猜更有用
    if C.panim then
        local pmnt = cur_montage(C.panim)
        if pmnt then out[#out + 1] = string.format('"p_montage":%q', pmnt) end
    end
    out[#out + 1] = '"near_pawns":' .. C.roster
    local action, arg = read_cmd()
    if action then
        local r = exec(action, arg)
        out[#out + 1] = string.format('"cmd_result":%q', r)
        log("cmd " .. action .. " -> " .. r)
    end
    out[#out + 1] = string.format('"write_enabled":%s,"tick_ms":%d,"tick":%d',
        tostring(file_exists(UNLOCK)), TICK_MS, tickNo)
    write_file(STATE, "{" .. table.concat(out, ",") .. "}")
    local cost = (os.clock() - t0) * 1000
    if cost > TICK_MS * 0.6 and slowWarned < 5 then
        slowWarned = slowWarned + 1
        log(string.format("CPU 时间 %.0fms/周期 %dms（os.clock 是进程 CPU，不是墙钟，会虚高）",
            cost, TICK_MS))
    end
end

local tickErrs = 0
local function safe_tick()
    local ok, err = pcall(tick)
    if not ok then
        if tickErrs < 5 then log("tick 失败（已跳过）: " .. tostring(err)) end
        tickErrs = tickErrs + 1
    end
end

local ok, err = pcall(function()
    LoopAsync(TICK_MS, safe_tick)
end)
if not ok then
    log("LoopAsync 不可用，改 ExecuteWithDelay 递归: " .. tostring(err))
    local function again() safe_tick(); ExecuteWithDelay(TICK_MS, again) end
    again()
end
log("已启动 v3.3（+敌人/玩家当前蒙太奇 montage/p_montage；probe anim）state 落 " .. STATE)
