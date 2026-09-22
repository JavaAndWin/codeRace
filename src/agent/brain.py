from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .grid import next_step
from .protocol import (
    BUILD_PRIORITY,
    BUY_PRIORITY,
    CONTROLLABLE_TYPES,
    COPPER,
    DAY_ROUNDS,
    IRON,
    MEDICINE,
    PIONEER,
    ROCKET,
    STATION,
    STATION_UPGRADE_VOUCHER_1,
    STATION_UPGRADE_VOUCHER_2,
    STONE,
    WALL,
    WALL_FIXER,
    WALL_UPGRADE_VOUCHER_1,
    WALL_UPGRADE_VOUCHER_2,
    WEAPON_UPGRADE_VOUCHER_1,
    WEAPON_UPGRADE_VOUCHER_2,
    WORKER,
    Pos,
    Turn,
    Unit,
    accept_task_command,
    attack_command,
    build_command,
    buy_command,
    collect_command,
    distance,
    drop_command,
    move_command,
    sell_command,
    station_footprint,
    submit_answer_command,
    summon_treasure_command,
    use_command,
)

LOGGER = logging.getLogger(__name__)

_state: dict[str, GameState] = {}

WEAPON_BUILD_COST = 25
WALL_BUILD_MATERIAL = STONE
WALL_FIXER_TARGET_COUNT = 2
EMERGENCY_FIXER_COUNT = 4
EMERGENCY_LAST_ROUNDS = 20
REPAIR_HP_THRESHOLD = 0.40
DAY_REPAIR_HP_THRESHOLD = 0.60
MIN_LEVEL_TO_REPAIR = 2
MAX_TOWERS = 3

NEIGHBOURS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
)

RESOURCE_PRIORITY_DEFAULT = (COPPER, IRON, STONE)


@dataclass
class GameState:
    team_id: str
    pending_llm: str | None = None
    price_signal: dict[str, int] = field(default_factory=dict)
    harvest_ban: dict[str, int] = field(default_factory=dict)
    treasure_plan: dict[str, Any] = field(default_factory=dict)
    task_state: dict[str, Any] = field(default_factory=dict)
    emergency: dict[str, Any] = field(default_factory=dict)
    operated_weapon_index: int = 0
    llm_official_day: int = 0
    llm_folk_day: int = 0
    llm_self_day: int = 0


def _get_state(team_id: str) -> GameState:
    key = team_id or "default"
    if key not in _state:
        _state[key] = GameState(team_id=key)
    return _state[key]


def decide(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str, str]:
    turn = Turn.load(payload)
    state = _get_state(turn.team_id)
    _ingest_llm_response(turn, state)

    commands: dict[int, dict[str, Any]] = {}
    prompt = ""
    execute_cmd = ""

    if turn.is_day:
        prompt, execute_cmd = _day(turn, state, commands)
    else:
        _night(turn, state, commands)

    return {str(k): v for k, v in commands.items()}, prompt, execute_cmd


def _ingest_llm_response(turn: Turn, state: GameState) -> None:
    if turn.last_cmd_result:
        state.task_state["last_cmd_result"] = turn.last_cmd_result

    if not turn.llm_resp:
        state.pending_llm = None
        return
    resp = turn.llm_resp.strip()
    if not resp:
        state.pending_llm = None
        return

    pending = state.pending_llm
    state.pending_llm = None

    try:
        start = resp.find("{")
        end = resp.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(resp[start:end + 1])
        else:
            data = json.loads(resp)
    except Exception:
        LOGGER.warning("failed to parse llmResp: %s", resp[:200])
        return

    if pending == "reasoning":
        for res in (STONE, IRON, COPPER):
            val = data.get(res)
            if isinstance(val, (int, float)):
                state.price_signal[res] = int(val)
            elif isinstance(val, str):
                state.price_signal[res] = _price_direction_from_text(val)
        ban = data.get("ban", [])
        if isinstance(ban, list):
            for item in ban:
                name = str(item).lower()
                if name in (STONE, IRON, COPPER):
                    state.harvest_ban[name] = 2
    elif pending == "long_context":
        state.treasure_plan = data
    elif pending == "self_evolution":
        if isinstance(data, dict):
            if "cmd" in data:
                state.task_state["pending_cmd"] = data["cmd"]
            if "answer" in data:
                state.task_state["answer"] = data["answer"]


def _price_direction_from_text(text: str) -> int:
    t = text.lower()
    if any(w in t for w in ("涨", "升", "up", "rise", "increase", "高")):
        return 1
    if any(w in t for w in ("跌", "降", "down", "fall", "decrease", "低")):
        return -1
    return 0


def _is_challenger(turn: Turn) -> bool:
    return turn.team_type == "challenger"


def _front_direction(turn: Turn) -> int:
    return 1 if _is_challenger(turn) else -1


def _station_bounds(turn: Turn) -> tuple[int, int, int, int]:
    station = turn.station()
    if station is None:
        return 0, 0, turn.width - 1, turn.height - 1
    fp = station_footprint(station.pos)
    xs = [p.x for p in fp]
    ys = [p.y for p in fp]
    return min(xs), min(ys), max(xs), max(ys)


def _c_wall_layout(turn: Turn) -> tuple[Pos, ...]:
    xmin, ymin, xmax, ymax = _station_bounds(turn)
    front = _front_direction(turn)
    back = -front

    fx = xmax + 2 if front == 1 else xmin - 2
    bx = xmin - 2 if front == 1 else xmax + 2
    ty = ymax + 2
    by = ymin - 2

    front_cells = [Pos(fx, y) for y in range(ymin - 2, ymax + 3)]
    cy1, cy2 = ymin, ymax
    front_cells.sort(
        key=lambda p: (
            0 if p.y in (cy1, cy2) else 1,
            abs(p.y - (ymin + ymax) / 2),
            p.y,
        )
    )

    top_cells = [Pos(x, ty) for x in range(xmin - 2, xmax + 3)]
    top_cells.sort(
        key=lambda p: (
            abs(p.x - (xmax + 2 if front == 1 else xmin - 2)),
            p.x,
        )
    )

    bottom_cells = [Pos(x, by) for x in range(xmin - 2, xmax + 3)]
    bottom_cells.sort(
        key=lambda p: (
            abs(p.x - (xmax + 2 if front == 1 else xmin - 2)),
            p.x,
        )
    )

    back_corner_cells = [Pos(bx, ymin - 2), Pos(bx, ymax + 2)]

    order = front_cells + top_cells + bottom_cells + back_corner_cells
    seen: set[Pos] = set()
    result: list[Pos] = []
    for pos in order:
        if pos in seen:
            continue
        seen.add(pos)
        if _is_valid_wall_pos(turn, pos):
            result.append(pos)
    return tuple(result)


def _is_valid_wall_pos(turn: Turn, pos: Pos) -> bool:
    if not turn.land(pos):
        return False
    station = turn.station()
    if station and pos in station_footprint(station.pos):
        return False
    return True


def _weapon_sites(turn: Turn) -> tuple[Pos, ...]:
    xmin, ymin, xmax, ymax = _station_bounds(turn)
    front = _front_direction(turn)

    if front == 1:
        back_x = xmin - 1
        side_back_choices = [Pos(back_x, ymin - 1), Pos(back_x, ymax + 1)]
        back_pair = [Pos(back_x, ymin), Pos(back_x, ymax)]
    else:
        back_x = xmax + 1
        side_back_choices = [Pos(back_x, ymin - 1), Pos(back_x, ymax + 1)]
        back_pair = [Pos(back_x, ymin), Pos(back_x, ymax)]

    side_back = None
    station = turn.station()
    fp = station_footprint(station.pos) if station else ()
    for p in side_back_choices:
        if p not in fp:
            side_back = p
            break
    if side_back is None:
        side_back = side_back_choices[0]

    sites = back_pair + [side_back]
    return tuple(sites)


def _neighbours(pos: Pos) -> tuple[Pos, ...]:
    return tuple(Pos(pos.x + dx, pos.y + dy) for dx, dy in NEIGHBOURS)


def _adjacent(pos1: Pos, pos2: Pos) -> bool:
    return distance(pos1, pos2) == 1


def _move_toward(
    turn: Turn,
    role: Unit,
    target: Pos,
    claimed: set[Pos],
    *,
    ignore_robots: bool = False,
) -> Pos | None:
    if role.pos == target:
        return None
    step = next_step(turn, role, target)
    if step is None or step in claimed:
        return None
    if not ignore_robots:
        for robot in turn.robots:
            if robot.pos == step:
                return None
    claimed.add(step)
    return step


def _move_near(
    turn: Turn,
    role: Unit,
    target: Pos,
    claimed: set[Pos],
    *,
    ignore_robots: bool = False,
) -> Pos | None:
    if _adjacent(role.pos, target) and turn.land(role.pos):
        return None
    stand = _find_adjacent_stand(turn, role, target, claimed)
    if stand is None:
        return None
    if role.pos == stand:
        return None
    return _move_toward(turn, role, stand, claimed, ignore_robots=ignore_robots)


def _find_adjacent_stand(
    turn: Turn,
    role: Unit,
    target: Pos,
    claimed: set[Pos],
    *,
    prefer_front: bool = False,
) -> Pos | None:
    station = turn.station()
    fp = station_footprint(station.pos) if station else ()
    blocked = turn.blocked(role)
    candidates = [
        p for p in _neighbours(target)
        if turn.land(p) and p not in blocked and (p == role.pos or p not in claimed)
    ]
    if not candidates:
        return None
    if prefer_front:
        front = _front_direction(turn)
        candidates.sort(
            key=lambda p: (
                -(p.x * front),
                distance(p, role.pos),
                p.x,
                p.y,
            )
        )
    else:
        candidates.sort(key=lambda p: (distance(p, role.pos), p.x, p.y))
    chosen = candidates[0]
    claimed.add(chosen)
    return chosen


def _resource_priority(turn: Turn, state: GameState) -> tuple[str, ...]:
    priority = []
    for res in RESOURCE_PRIORITY_DEFAULT:
        if state.harvest_ban.get(res, 0) > 0:
            priority.append((-1000, res))
            continue
        signal = state.price_signal.get(res, 0)
        priority.append((signal, res))
    priority.sort(key=lambda x: x[0], reverse=True)
    return tuple(x[1] for x in priority)


def _is_banned(res: str, state: GameState) -> bool:
    return state.harvest_ban.get(res, 0) > 0


def _count_item(unit: Unit, name: str) -> int:
    return unit.backpack.count(name)


def _backpack_free(unit: Unit) -> int:
    if unit.capacity is None:
        return 100
    return unit.capacity - len(unit.backpack)


def _should_sell(unit: Unit) -> bool:
    return _backpack_free(unit) <= 2 or len(unit.backpack) >= 40


def _ore_value(unit: Unit, shop: dict[str, int]) -> int:
    return sum(shop.get(item, 0) for item in unit.backpack if item in (STONE, IRON, COPPER))


def _move_to_sell(
    turn: Turn,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    vendor = turn.vendor_pos()
    if vendor is None:
        return False
    if _adjacent(role.pos, vendor):
        for item in (COPPER, IRON, STONE):
            cnt = _count_item(role, item)
            if cnt:
                commands[role.unit_id] = sell_command(item, cnt)
                return True
        return False
    step = _move_near(turn, role, vendor, claimed)
    if step is not None:
        commands[role.unit_id] = move_command(step)
        return True
    return False


def _move_to_shop(
    turn: Turn,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    shop = turn.weapon_shop_pos()
    if shop is None:
        return False
    if _adjacent(role.pos, shop):
        return False
    step = _move_near(turn, role, shop, claimed)
    if step is not None:
        commands[role.unit_id] = move_command(step)
        return True
    return False


def _collect_resource(
    turn: Turn,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
    priority: tuple[str, ...] | None = None,
    state: GameState | None = None,
) -> bool:
    if role.backpack_full:
        return False
    order = priority or RESOURCE_PRIORITY_DEFAULT
    for res in order:
        if state is not None and _is_banned(res, state):
            continue
        cells = turn.resource_cells(res)
        cells = sorted(cells, key=lambda p: distance(role.pos, p))
        for cell in cells:
            if cell in claimed:
                continue
            if _adjacent(role.pos, cell):
                commands[role.unit_id] = collect_command(cell)
                claimed.add(cell)
                return True
            step = _move_near(turn, role, cell, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)
                return True
    return False


def _missing_walls(turn: Turn) -> list[Pos]:
    layout = _c_wall_layout(turn)
    standing = {w.pos for w in turn.walls()}
    return [p for p in layout if p not in standing]


def _wall_needing_repair(turn: Turn, threshold: float, min_level: int) -> Unit | None:
    layout = _c_wall_layout(turn)
    wall_by_pos = {w.pos: w for w in turn.walls()}
    for pos in layout:
        wall = wall_by_pos.get(pos)
        if wall is None or wall.level < min_level:
            continue
        max_hp = {1: 1000, 2: 1500, 3: 2000}.get(wall.level, 1000)
        if wall.health / max_hp < threshold:
            return wall
    return None


def _wall_needing_upgrade(turn: Turn) -> Unit | None:
    layout = _c_wall_layout(turn)
    positions = {w.pos: w for w in turn.walls()}
    for pos in layout:
        wall = positions.get(pos)
        if wall and wall.level < 3:
            return wall
    return None


def _tower_needing_upgrade(turn: Turn) -> Unit | None:
    for tower in turn.towers():
        if tower.level < 3:
            return tower
    return None


def _station_needing_upgrade(turn: Turn) -> Unit | None:
    station = turn.station()
    if station and station.level < 3:
        return station
    return None


def _item_price(turn: Turn, name: str) -> int:
    return turn.weapon_shop.get(name, 0)


def _can_afford(turn: Turn, name: str, num: int = 1) -> bool:
    return turn.gold >= _item_price(turn, name) * num


def _has_voucher(unit: Unit, name: str) -> bool:
    return name in unit.backpack


def _select_buy_item(turn: Turn, unit: Unit) -> str | None:
    for name in BUY_PRIORITY:
        if name == WALL_FIXER:
            continue
        if _has_voucher(unit, name):
            continue
        if _can_afford(turn, name):
            return name
    fixers = _count_item(unit, WALL_FIXER)
    if fixers < WALL_FIXER_TARGET_COUNT and _can_afford(turn, WALL_FIXER):
        return WALL_FIXER
    return None


def _try_use_voucher(
    turn: Turn,
    role: Unit,
    commands: dict[int, dict[str, Any]],
) -> bool:
    if WEAPON_UPGRADE_VOUCHER_1 in role.backpack or WEAPON_UPGRADE_VOUCHER_2 in role.backpack:
        for tower in turn.towers():
            if not _adjacent(role.pos, tower.pos):
                continue
            if tower.level == 1 and WEAPON_UPGRADE_VOUCHER_1 in role.backpack:
                commands[role.unit_id] = use_command(WEAPON_UPGRADE_VOUCHER_1, tower.pos)
                return True
            if tower.level == 2 and WEAPON_UPGRADE_VOUCHER_2 in role.backpack:
                commands[role.unit_id] = use_command(WEAPON_UPGRADE_VOUCHER_2, tower.pos)
                return True
    if WALL_UPGRADE_VOUCHER_1 in role.backpack or WALL_UPGRADE_VOUCHER_2 in role.backpack:
        wall = _wall_needing_upgrade(turn)
        if wall and _adjacent(role.pos, wall.pos):
            if wall.level == 1 and WALL_UPGRADE_VOUCHER_1 in role.backpack:
                commands[role.unit_id] = use_command(WALL_UPGRADE_VOUCHER_1, wall.pos)
                return True
            if wall.level == 2 and WALL_UPGRADE_VOUCHER_2 in role.backpack:
                commands[role.unit_id] = use_command(WALL_UPGRADE_VOUCHER_2, wall.pos)
                return True
    station = turn.station()
    if station:
        if station.level == 1 and STATION_UPGRADE_VOUCHER_1 in role.backpack:
            if _adjacent(role.pos, station.pos) or _adjacent(role.pos, _closest_station_cell(station, role.pos)):
                commands[role.unit_id] = use_command(STATION_UPGRADE_VOUCHER_1, station.pos)
                return True
        if station.level == 2 and STATION_UPGRADE_VOUCHER_2 in role.backpack:
            if _adjacent(role.pos, station.pos) or _adjacent(role.pos, _closest_station_cell(station, role.pos)):
                commands[role.unit_id] = use_command(STATION_UPGRADE_VOUCHER_2, station.pos)
                return True
    return False


def _closest_station_cell(station: Unit, pos: Pos) -> Pos:
    fp = station_footprint(station.pos)
    return min(fp, key=lambda p: distance(p, pos))


def _try_repair_wall(
    turn: Turn,
    role: Unit,
    threshold: float,
    min_level: int,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    wall = _wall_needing_repair(turn, threshold, min_level)
    if wall is None:
        return False
    if WALL_FIXER not in role.backpack:
        return False
    if _adjacent(role.pos, wall.pos):
        commands[role.unit_id] = use_command(WALL_FIXER, wall.pos)
        return True
    stand = _find_adjacent_stand(turn, role, wall.pos, claimed, prefer_front=True)
    if stand is not None and stand != role.pos:
        step = _move_toward(turn, role, stand, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return True
    return False


def _try_build_wall(
    turn: Turn,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    missing = _missing_walls(turn)
    if not missing:
        return False
    stones = _count_item(role, STONE)
    if stones == 0:
        return False
    for pos in missing:
        if pos in claimed:
            continue
        if _adjacent(role.pos, pos):
            commands[role.unit_id] = build_command(pos, WALL)
            claimed.add(pos)
            return True
        stand = _find_adjacent_stand(turn, role, pos, claimed, prefer_front=True)
        if stand is not None:
            if role.pos == stand:
                commands[role.unit_id] = build_command(pos, WALL)
                claimed.add(pos)
                return True
            step = _move_toward(turn, role, stand, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)
                return True
    return False


def _try_build_weapon(
    turn: Turn,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    if turn.gold < WEAPON_BUILD_COST:
        return False
    towers = turn.towers()
    site_tower = {t.pos: t for t in towers}
    rocket_count = sum(1 for t in towers if t.kind == ROCKET)
    if rocket_count >= MAX_TOWERS:
        return False
    sites = _weapon_sites(turn)
    for pos in sites:
        if pos in claimed:
            continue
        existing = site_tower.get(pos)
        if existing and existing.kind == ROCKET:
            continue
        if existing is None and len(towers) >= MAX_TOWERS:
            continue
        if _adjacent(role.pos, pos):
            commands[role.unit_id] = build_command(pos, ROCKET)
            claimed.add(pos)
            return True
        stand = _find_adjacent_stand(turn, role, pos, claimed)
        if stand is not None:
            if role.pos == stand:
                commands[role.unit_id] = build_command(pos, ROCKET)
                claimed.add(pos)
                return True
            step = _move_toward(turn, role, stand, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)
                return True
    return False


def _day(
    turn: Turn,
    state: GameState,
    commands: dict[int, dict[str, Any]],
) -> tuple[str, str]:
    claimed: set[Pos] = set()
    workers = list(turn.workers())
    pioneer = turn.pioneer()

    if turn.round_in_day == 1:
        for res in list(state.harvest_ban):
            state.harvest_ban[res] -= 1
            if state.harvest_ban[res] <= 0:
                del state.harvest_ban[res]

    emergency_repairer_id = _handle_emergency_repair_prep(turn, state, workers, claimed, commands)
    in_emergency = emergency_repairer_id is not None

    prompt, execute_cmd = _plan_llm(turn, state)

    if pioneer and not in_emergency:
        _pioneer_day(turn, state, pioneer, claimed, commands)

    for role in workers:
        if role.unit_id in commands or role.unit_id == emergency_repairer_id:
            continue
        _worker_day_priority(turn, state, role, claimed, commands)

    for role in workers:
        if role.unit_id in commands or role.unit_id == emergency_repairer_id:
            continue
        _worker_economy(turn, state, role, claimed, commands)

    return prompt, execute_cmd


def _worker_day_priority(
    turn: Turn,
    state: GameState,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    if _try_build_weapon(turn, role, claimed, commands):
        return True

    missing = _missing_walls(turn)
    if missing:
        stones = _count_item(role, STONE)
        if stones:
            if _try_build_wall(turn, role, claimed, commands):
                return True
        if _collect_resource(turn, role, claimed, commands, (STONE,), state=state):
            return True

    if _try_use_voucher(turn, role, commands):
        return True

    if WALL_UPGRADE_VOUCHER_1 in role.backpack or WALL_UPGRADE_VOUCHER_2 in role.backpack:
        wall = _wall_needing_upgrade(turn)
        if wall:
            if _adjacent(role.pos, wall.pos):
                return _try_use_voucher(turn, role, commands)
            stand = _find_adjacent_stand(turn, role, wall.pos, claimed, prefer_front=True)
            if stand is not None and stand != role.pos:
                step = _move_toward(turn, role, stand, claimed)
                if step is not None:
                    commands[role.unit_id] = move_command(step)
                    return True

    if STATION_UPGRADE_VOUCHER_1 in role.backpack or STATION_UPGRADE_VOUCHER_2 in role.backpack:
        station = turn.station()
        if station:
            target = _closest_station_cell(station, role.pos)
            if _adjacent(role.pos, target):
                return _try_use_voucher(turn, role, commands)
            stand = _find_adjacent_stand(turn, role, target, claimed)
            if stand is not None and stand != role.pos:
                step = _move_toward(turn, role, stand, claimed)
                if step is not None:
                    commands[role.unit_id] = move_command(step)
                    return True

    shop = turn.weapon_shop_pos()
    if shop and _adjacent(role.pos, shop):
        item = _select_buy_item(turn, role)
        if item:
            commands[role.unit_id] = buy_command(item)
            return True

    return False


def _worker_economy(
    turn: Turn,
    state: GameState,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> None:
    if _try_use_voucher(turn, role, commands):
        return
    if _try_repair_wall(turn, role, REPAIR_HP_THRESHOLD, MIN_LEVEL_TO_REPAIR, claimed, commands):
        return

    item = _select_buy_item(turn, role)
    if item and _backpack_free(role) >= 1:
        if _move_to_shop(turn, role, claimed, commands):
            return
        shop = turn.weapon_shop_pos()
        if shop and _adjacent(role.pos, shop):
            commands[role.unit_id] = buy_command(item)
            return

    if _should_sell(role) and _ore_value(role, turn.vendor_shop) > 0:
        if _move_to_sell(turn, role, claimed, commands):
            return

    priority = _resource_priority(turn, state)
    if _collect_resource(turn, role, claimed, commands, priority, state=state):
        return

    _move_to_sell(turn, role, claimed, commands)


def _level2_plus_wall_count(turn: Turn) -> int:
    return sum(1 for w in turn.walls() if w.level >= 2)


def _handle_emergency_repair_prep(
    turn: Turn,
    state: GameState,
    workers: list[Unit],
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> int | None:
    if not turn.is_day:
        return None
    if turn.round_in_day <= DAY_ROUNDS - EMERGENCY_LAST_ROUNDS:
        return None
    if _level2_plus_wall_count(turn) < 4:
        return None

    if not workers:
        return None

    repairer = min(workers, key=lambda r: distance(r.pos, _wall_center(turn)))
    fixers = _count_item(repairer, WALL_FIXER)

    if _ore_value(repairer, turn.vendor_shop) > 0:
        if _move_to_sell(turn, repairer, claimed, commands):
            return repairer.unit_id

    if fixers < EMERGENCY_FIXER_COUNT:
        shop = turn.weapon_shop_pos()
        if shop is None:
            return None
        if _adjacent(repairer.pos, shop):
            free = _backpack_free(repairer)
            need = EMERGENCY_FIXER_COUNT - fixers
            if free < need:
                for item in list(repairer.backpack):
                    if item != WALL_FIXER:
                        commands[repairer.unit_id] = drop_command(item)
                        return repairer.unit_id
                return repairer.unit_id
            commands[repairer.unit_id] = buy_command(WALL_FIXER, min(need, free))
            return repairer.unit_id
        step = _move_near(turn, repairer, shop, claimed)
        if step is not None:
            commands[repairer.unit_id] = move_command(step)
            return repairer.unit_id
        return None

    wall = _wall_needing_repair(turn, DAY_REPAIR_HP_THRESHOLD, MIN_LEVEL_TO_REPAIR)
    if wall is not None and _adjacent(repairer.pos, wall.pos):
        commands[repairer.unit_id] = use_command(WALL_FIXER, wall.pos)
        return repairer.unit_id

    standby = _wall_standby_pos(turn)
    if standby is not None and repairer.pos != standby:
        step = _move_toward(turn, repairer, standby, claimed)
        if step is not None:
            commands[repairer.unit_id] = move_command(step)
            return repairer.unit_id

    return repairer.unit_id


def _wall_center(turn: Turn) -> Pos:
    station = turn.station()
    if station is None:
        return Pos(turn.width // 2, turn.height // 2)
    fp = station_footprint(station.pos)
    xs = [p.x for p in fp]
    ys = [p.y for p in fp]
    return Pos(sum(xs) // len(xs), sum(ys) // len(ys))


def _wall_standby_pos(turn: Turn) -> Pos | None:
    xmin, ymin, xmax, ymax = _station_bounds(turn)
    back = -_front_direction(turn)
    bx = xmin - 2 if back == -1 else xmax + 2
    candidates = [Pos(bx, ymin - 1), Pos(bx, ymin), Pos(bx, ymax), Pos(bx, ymax + 1)]
    for p in candidates:
        if turn.land(p) and not _is_occupied_by_us(turn, p):
            return p
    return None


def _is_occupied_by_us(turn: Turn, pos: Pos) -> bool:
    return pos in turn.occupied_cells()


def _pioneer_day(
    turn: Turn,
    state: GameState,
    pioneer: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> None:
    if state.treasure_plan:
        if _execute_treasure_plan(turn, pioneer, claimed, commands, state):
            return

    if _handle_self_evolution(turn, state, pioneer, claimed, commands):
        return

    task_pos = _available_task_point(turn)
    if task_pos:
        if _adjacent(pioneer.pos, task_pos):
            commands[pioneer.unit_id] = accept_task_command()
            return
        step = _move_near(turn, pioneer, task_pos, claimed)
        if step is not None:
            commands[pioneer.unit_id] = move_command(step)
            return


def _available_task_point(turn: Turn) -> Pos | None:
    for task in turn.player_tasks:
        if task.is_valid and task.cooldown == 0:
            return task.pos
    return None


def _handle_self_evolution(
    turn: Turn,
    state: GameState,
    pioneer: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> bool:
    task_pos = _available_task_point(turn)
    if task_pos is None:
        return False
    if not _adjacent(pioneer.pos, task_pos):
        step = _move_near(turn, pioneer, task_pos, claimed)
        if step is not None:
            commands[pioneer.unit_id] = move_command(step)
        return True
    if turn.phase_task:
        answer = state.task_state.get("answer")
        if answer:
            commands[pioneer.unit_id] = submit_answer_command(str(answer))
            state.task_state.pop("answer", None)
            return True
        return True
    commands[pioneer.unit_id] = accept_task_command()
    return True


def _execute_treasure_plan(
    turn: Turn,
    pioneer: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
    state: GameState,
) -> bool:
    plan = state.treasure_plan
    loc = plan.get("location")
    items = plan.get("items", [])

    last_result = turn.last_summon_treasure_result
    if last_result in (3, 4):
        state.treasure_plan = {}
        return False

    if not loc or not items:
        state.treasure_plan = {}
        return False

    if isinstance(loc, dict):
        loc = Pos(int(loc["x"]), int(loc["y"]))
    if not isinstance(loc, Pos):
        state.treasure_plan = {}
        return False

    if not (0 <= loc.x < turn.width and 0 <= loc.y < turn.height):
        state.treasure_plan = {}
        return False
    station = turn.station()
    if station and loc in station_footprint(station.pos):
        state.treasure_plan = {}
        return False

    shop_items = set(turn.weapon_shop.keys())
    if any(item not in shop_items for item in items):
        state.treasure_plan = {}
        return False

    missing = [i for i in items if i not in pioneer.backpack]
    if missing:
        shop = turn.weapon_shop_pos()
        if shop and _adjacent(pioneer.pos, shop):
            item = missing[0]
            if _can_afford(turn, item):
                commands[pioneer.unit_id] = buy_command(item)
            else:
                state.treasure_plan = {}
            return True
        if shop:
            step = _move_near(turn, pioneer, shop, claimed)
            if step is not None:
                commands[pioneer.unit_id] = move_command(step)
                return True
        state.treasure_plan = {}
        return False

    if not _adjacent(pioneer.pos, loc):
        step = _move_near(turn, pioneer, loc, claimed)
        if step is not None:
            commands[pioneer.unit_id] = move_command(step)
            return True
        state.treasure_plan = {}
        return False

    if not _treasure_time_ready(plan, turn):
        return True

    commands[pioneer.unit_id] = summon_treasure_command(loc, items)
    state.treasure_plan = {}
    return True


def _treasure_time_ready(plan: dict[str, Any], turn: Turn) -> bool:
    time_str = plan.get("time")
    if not time_str:
        return True
    if not isinstance(time_str, str):
        return True
    match = re.search(r"day\s*(\d+).*?round\s*(\d+)", time_str, re.IGNORECASE)
    if not match:
        return True
    planned_day = int(match.group(1))
    planned_round = int(match.group(2))
    if turn.day < planned_day:
        return False
    if turn.day == planned_day and turn.round_in_day < planned_round:
        return False
    return True


def _plan_llm(turn: Turn, state: GameState) -> tuple[str, str]:
    if state.pending_llm:
        return "", ""

    pending_cmd = state.task_state.get("pending_cmd")
    if pending_cmd:
        state.task_state.pop("pending_cmd", None)
        return "", str(pending_cmd)

    if turn.is_day and state.llm_official_day < turn.day:
        news = turn.world_news.official.strip()
        if news and not _is_placeholder_news(news):
            prompt = _reasoning_prompt(news)
            state.pending_llm = "reasoning"
            state.llm_official_day = turn.day
            return prompt, ""

    task = turn.phase_task
    has_cmd_result = bool(state.task_state.get("last_cmd_result"))
    if turn.is_day and task and (state.llm_self_day < turn.day or has_cmd_result):
        last_result = state.task_state.pop("last_cmd_result", "")
        prompt = _self_evolution_prompt(task, last_result)
        state.pending_llm = "self_evolution"
        state.llm_self_day = turn.day
        return prompt, ""

    if turn.is_day and state.llm_folk_day < turn.day:
        folk = turn.world_news.folk.strip()
        if folk and not _is_placeholder_folk(folk):
            prompt = _long_context_prompt(folk, state)
            state.pending_llm = "long_context"
            state.llm_folk_day = turn.day
            return prompt, ""

    return "", ""


def _is_placeholder_news(news: str) -> bool:
    return "无重大" in news or "今日无" in news or news in ("无", "今日无重大新闻")


def _is_placeholder_folk(folk: str) -> bool:
    return not folk or folk in ("无", "暂无传闻")


def _reasoning_prompt(news: str) -> str:
    return (
        "你正在玩一个资源交易游戏。官方消息可能影响矿石收购价格以及未来几天能否采集某些矿石。"
        "请根据以下官方消息，判断今天之后石头(stone)、铁矿(iron)、铜矿(copper)的收购价格变化趋势，"
        "以及未来2天内哪些资源将无法采集。"
        "只返回一个JSON对象，格式固定为："
        "{\"stone\": -1/0/1, \"iron\": -1/0/1, \"copper\": -1/0/1, "
        "\"ban\": [\"resource_name\", ...]}。"
        "其中价格1表示上涨，0表示不变，-1表示下跌；ban数组列出明天和后天无法采集的资源名称（stone/iron/copper），"
        "如果可以正常采集则ban为空数组。不要输出其他内容。\n\n"
        f"官方消息：{news}"
    )


def _long_context_prompt(folk: str, state: GameState) -> str:
    history = state.task_state.get("folk_history", [])
    history.append(folk)
    state.task_state["folk_history"] = history
    joined = "\n".join(history)
    return (
        "你正在分析民间传闻以寻找宝藏。请根据以下传闻，推断宝藏坐标、开启所需物品、开启时间。"
        "只返回一个JSON对象，格式固定为："
        "{\"location\": {\"x\": int, \"y\": int}, \"items\": [\"ItemName1\", ...], \"time\": \"day N round M\", \"confidence\": 0-1}。"
        "如果信息不足，confidence填0并给出最可能猜测。不要输出其他内容。\n\n"
        f"传闻：{joined}"
    )


def _self_evolution_prompt(task: str, last_cmd_result: str = "") -> str:
    extra = ""
    if last_cmd_result:
        extra = (
            "\n\n你上一步执行的沙盒命令及其结果如下：\n"
            f"{last_cmd_result}\n"
            "请根据该结果决定下一步：修正命令、给出最终答案，或继续探索。"
        )
    return (
        "你是一名开拓者，正在执行自进化任务。请根据任务描述给出任务答案或下一步沙盒命令。"
        "如果可以直接给出答案，请返回JSON：{\"answer\": \"你的答案\"}。"
        "如果需要执行沙盒命令，请返回JSON：{\"cmd\": \"要执行的shell/python命令\"}。"
        "不要输出其他内容。\n\n"
        f"任务描述：{task}"
        f"{extra}"
    )


def _night(
    turn: Turn,
    state: GameState,
    commands: dict[int, dict[str, Any]],
) -> None:
    claimed: set[Pos] = set()
    pioneer = turn.pioneer()

    if pioneer and pioneer.health > 0:
        _pioneer_operate_weapons(turn, state, pioneer, claimed, commands)

    workers = list(turn.workers())
    repairer_id = _assign_night_repair(turn, workers, claimed, commands)

    for role in workers:
        if role.unit_id in commands:
            continue
        _worker_night(turn, state, role, claimed, commands)


def _assign_night_repair(
    turn: Turn,
    workers: list[Unit],
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> int | None:
    wall_to_repair = _wall_needing_repair(turn, REPAIR_HP_THRESHOLD, MIN_LEVEL_TO_REPAIR)
    if wall_to_repair is None:
        return None
    candidates = [w for w in workers if WALL_FIXER in w.backpack]
    if not candidates:
        return None
    repairer = min(candidates, key=lambda w: distance(w.pos, wall_to_repair.pos))
    if _try_repair_wall(turn, repairer, REPAIR_HP_THRESHOLD, MIN_LEVEL_TO_REPAIR, claimed, commands):
        return repairer.unit_id
    return None


def _pioneer_operate_weapons(
    turn: Turn,
    state: GameState,
    pioneer: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> None:
    towers = turn.towers()
    if not towers:
        return

    index = state.operated_weapon_index % max(len(towers), 1)
    ordered = list(towers[index:]) + list(towers[:index])

    for tower in ordered:
        if tower.cooldown > 0:
            continue
        if _adjacent(pioneer.pos, tower.pos):
            if tower.kind == ROCKET:
                targets = _rocket_targets(tower, turn.robots)
                if targets:
                    commands[tower.unit_id] = attack_command(pioneer.unit_id, *targets)
                    state.operated_weapon_index = (towers.index(tower) + 1) % len(towers)
                    return
            else:
                target = _attack_target(turn, tower)
                if target is not None:
                    commands[tower.unit_id] = attack_command(pioneer.unit_id, target)
                    state.operated_weapon_index = (towers.index(tower) + 1) % len(towers)
                    return
        stand = _find_adjacent_stand(turn, pioneer, tower.pos, claimed)
        if stand is not None:
            if pioneer.pos == stand:
                return
            step = _move_toward(turn, pioneer, stand, claimed, ignore_robots=True)
            if step is not None:
                commands[pioneer.unit_id] = move_command(step)
                return

    tower = towers[0]
    if not _adjacent(pioneer.pos, tower.pos):
        stand = _find_adjacent_stand(turn, pioneer, tower.pos, claimed)
        if stand is not None and stand != pioneer.pos:
            step = _move_toward(turn, pioneer, stand, claimed, ignore_robots=True)
            if step is not None:
                commands[pioneer.unit_id] = move_command(step)


def _attack_target(turn: Turn, tower: Unit) -> Pos | None:
    reach = tower.range_of_attack()
    targets = [
        robot for robot in turn.robots
        if robot.health > 0 and distance(tower.pos, robot.pos) <= reach
    ]
    if not targets:
        return None
    value = {"smallRobot": 1, "middleRobot": 2, "largeRobot": 4, "bossRobot": 10}
    targets.sort(
        key=lambda robot: (
            -value.get(robot.kind, 0),
            distance(tower.pos, robot.pos),
        )
    )
    return targets[0].pos


def _rocket_targets(tower: Unit, robots: tuple[Robot, ...]) -> list[Pos]:
    reach = tower.range_of_attack()
    candidates = [
        robot for robot in robots
        if robot.health > 0 and distance(tower.pos, robot.pos) <= reach
    ]
    if not candidates:
        return []

    def damage_score(target: Robot) -> int:
        score = 20
        for other in candidates:
            if other is target:
                continue
            if distance(target.pos, other.pos) <= 1:
                score += 10
        return score

    candidates.sort(
        key=lambda robot: (
            -damage_score(robot),
            distance(tower.pos, robot.pos),
            robot.robot_id,
        )
    )
    count = max(1, min(tower.level, 3))
    return [robot.pos for robot in candidates[:count]]


def _worker_night(
    turn: Turn,
    state: GameState,
    role: Unit,
    claimed: set[Pos],
    commands: dict[int, dict[str, Any]],
) -> None:
    nearest_robot_dist = min(
        (distance(role.pos, robot.pos) for robot in turn.robots),
        default=99,
    )
    if nearest_robot_dist <= 2:
        safe = _safe_escape_step(turn, role, claimed)
        if safe is not None:
            commands[role.unit_id] = move_command(safe)
            return

    if _should_sell(role) and _ore_value(role, turn.vendor_shop) > 0:
        if _move_to_sell(turn, role, claimed, commands):
            return
    priority = _resource_priority(turn, state)
    if _collect_resource(turn, role, claimed, commands, priority, state=state):
        return
    standby = _wall_standby_pos(turn)
    if standby is not None and role.pos != standby:
        step = _move_toward(turn, role, standby, claimed, ignore_robots=True)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return


def _safe_escape_step(turn: Turn, role: Unit, claimed: set[Pos]) -> Pos | None:
    back_dir = -_front_direction(turn)
    best: Pos | None = None
    best_score = -10**9
    blocked = turn.blocked(role)
    for dx, dy in NEIGHBOURS:
        p = Pos(role.pos.x + dx, role.pos.y + dy)
        if p in blocked or p in claimed or not turn.land(p):
            continue
        back_score = dx * back_dir
        robot_dist = min(
            (distance(p, robot.pos) for robot in turn.robots),
            default=0,
        )
        score = back_score * 10 + robot_dist
        if score > best_score:
            best_score = score
            best = p
    if best is not None:
        claimed.add(best)
    return best
