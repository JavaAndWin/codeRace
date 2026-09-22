from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DAY_ROUNDS = 70
NIGHT_ROUNDS = 60
ROUNDS_PER_DAY = DAY_ROUNDS + NIGHT_ROUNDS

WEAPON_BUILD_COST = 25

LAND = "land"
STATION = "station"
WALL = "wall"

WORKER = "worker"
PIONEER = "pioneer"
GATLING = "gatling"
RAILGUN = "railgun"
ROCKET = "rocket"
TOWER_TYPES = (GATLING, RAILGUN, ROCKET)
CONTROLLABLE_TYPES = (WORKER, PIONEER)

TOWER_RANGE_BY_LEVEL = {
    GATLING: (3, 5, 7),
    RAILGUN: (6, 8, 10),
    ROCKET: (10, 15, 10**9),
}

STONE = "stone"
IRON = "iron"
COPPER = "copper"
WALL_MATERIAL = STONE

WALL_FIXER = "WallFixer"
MEDICINE = "Medicine"
WEAPON_UPGRADE_VOUCHER_1 = "WeaponUpgradeVoucher1"
WEAPON_UPGRADE_VOUCHER_2 = "WeaponUpgradeVoucher2"
WALL_UPGRADE_VOUCHER_1 = "WallUpgradeVoucher1"
WALL_UPGRADE_VOUCHER_2 = "WallUpgradeVoucher2"
STATION_UPGRADE_VOUCHER_1 = "StationUpgradeVoucher1"
STATION_UPGRADE_VOUCHER_2 = "StationUpgradeVoucher2"

BUY_PRIORITY = (
    WEAPON_UPGRADE_VOUCHER_1,
    WEAPON_UPGRADE_VOUCHER_2,
    WALL_UPGRADE_VOUCHER_1,
    WALL_UPGRADE_VOUCHER_2,
    STATION_UPGRADE_VOUCHER_1,
    STATION_UPGRADE_VOUCHER_2,
    WALL_FIXER,
)

BUILD_PRIORITY = (
    "weapon_build",
    "wall_build",
    "weapon_upgrade",
    "wall_upgrade",
    "station_upgrade",
)

SELF_EVOLUTION_1 = "自进化类1"
SELF_EVOLUTION_2 = "自进化类2"

WALL_MAX_HEALTH = {1: 1000, 2: 1500, 3: 2000}
WEAPON_MAX_HEALTH = {1: 1000, 2: 1500, 3: 2000}


@dataclass(frozen=True, slots=True)
class Pos:
    x: int
    y: int

    @classmethod
    def load(cls, raw: Any) -> Pos:
        return cls(int(raw["x"]), int(raw["y"]))

    def dump(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


def distance(first: Pos, second: Pos) -> int:
    return max(abs(first.x - second.x), abs(first.y - second.y))


def station_footprint(pos: Pos) -> tuple[Pos, ...]:
    return (
        pos,
        Pos(pos.x + 1, pos.y),
        Pos(pos.x, pos.y - 1),
        Pos(pos.x + 1, pos.y - 1),
    )


@dataclass(frozen=True, slots=True)
class Unit:
    unit_id: int
    pos: Pos
    kind: str
    health: int
    level: int
    cooldown: int
    attack_range: int
    capacity: int | None
    backpack: tuple[str, ...]

    @classmethod
    def load(cls, raw: dict[str, Any]) -> Unit:
        raw_capacity = raw.get("backPackCapability")
        return cls(
            int(raw.get("id") or 0),
            Pos.load(raw["pos"]),
            str(raw["roleType"]),
            int(raw["health"]),
            int(raw.get("level") or 0),
            int(raw.get("cooldown") or 0),
            int(raw.get("attackRange") or 0),
            int(raw_capacity) if raw_capacity is not None else None,
            tuple(str(item) for item in raw.get("backpack") or ()),
        )

    @property
    def backpack_full(self) -> bool:
        if self.capacity is None:
            return False
        return len(self.backpack) >= self.capacity

    def range_of_attack(self) -> int:
        if self.attack_range > 0:
            return self.attack_range
        table = TOWER_RANGE_BY_LEVEL.get(self.kind)
        if table is None:
            return 0
        level = min(max(self.level, 1), len(table))
        return table[level - 1]


@dataclass(frozen=True, slots=True)
class Robot:
    robot_id: int
    pos: Pos
    health: int
    kind: str = ""

    @classmethod
    def load(cls, raw: dict[str, Any]) -> Robot:
        return cls(
            int(raw["id"]),
            Pos.load(raw["pos"]),
            int(raw["health"]),
            str(raw.get("roleType") or ""),
        )


@dataclass(frozen=True, slots=True)
class PlayerTask:
    task_type: str
    pos: Pos
    cooldown: int
    score_reward: int
    gold_reward: int
    is_valid: bool
    timeout_rounds: int

    @classmethod
    def load(cls, raw: dict[str, Any]) -> PlayerTask:
        return cls(
            task_type=str(raw.get("taskType") or ""),
            pos=Pos.load(raw["taskPosition"]),
            cooldown=int(raw.get("coldDownRounds") or 0),
            score_reward=int(raw.get("scoreReward") or 0),
            gold_reward=int(raw.get("goldReward") or 0),
            is_valid=bool(raw.get("isValid")),
            timeout_rounds=int(raw.get("timeoutRounds") or 0),
        )


@dataclass(frozen=True, slots=True)
class WorldNews:
    official: str
    folk: str

    @classmethod
    def load(cls, raw: dict[str, Any] | None) -> WorldNews:
        if not raw:
            return cls("", "")
        return cls(
            official=str(raw.get("officialNews") or ""),
            folk=str(raw.get("folkLegends") or ""),
        )


@dataclass(frozen=True, slots=True)
class Turn:
    round_no: int
    is_day: bool
    day: int
    round_in_day: int
    gold: int
    width: int
    height: int
    zones: dict[Pos, str]
    ours: tuple[Unit, ...]
    robots: tuple[Robot, ...]
    team_id: str
    team_type: str
    player_tasks: tuple[PlayerTask, ...]
    world_news: WorldNews
    vendor_shop: dict[str, int]
    weapon_shop: dict[str, int]
    last_action_results: dict[int, bool]
    last_summon_treasure_result: int
    llm_resp: str
    last_cmd_result: str
    phase_task: str
    enemy_roles: tuple[Unit, ...]

    @classmethod
    def load(cls, payload: dict[str, Any]) -> Turn:
        round_no = int(payload["roundNo"])
        info = payload["mapInfo"]
        team = payload["teamOur"]
        zones: dict[Pos, str] = {
            Pos.load(zone["pos"]): str(zone["neutralType"])
            for zone in info.get("zones") or ()
        }
        return cls(
            round_no=round_no,
            is_day=(round_no - 1) % ROUNDS_PER_DAY < DAY_ROUNDS,
            day=(round_no - 1) // ROUNDS_PER_DAY + 1,
            round_in_day=(round_no - 1) % ROUNDS_PER_DAY + 1,
            gold=int(team.get("goldNum") or 0),
            width=int(info["width"]),
            height=int(info["height"]),
            zones=zones,
            ours=tuple(Unit.load(role) for role in team.get("roles") or ()),
            robots=tuple(
                Robot.load(robot)
                for robot in (payload.get("robot") or {}).get("roles") or ()
            ),
            team_id=str(team.get("teamId") or ""),
            team_type=str(team.get("type") or ""),
            player_tasks=tuple(
                PlayerTask.load(task) for task in team.get("playerTasks") or ()
            ),
            world_news=WorldNews.load(payload.get("worldNews")),
            vendor_shop={
                item["name"]: int(item["price"])
                for item in (payload.get("vendorShopList") or ())
            },
            weapon_shop={
                item["name"]: int(item["price"])
                for item in (payload.get("weaponShopList") or ())
            },
            last_action_results={
                int(k): bool(v)
                for k, v in (payload.get("lastRoundRoleActionResults") or {}).items()
            },
            last_summon_treasure_result=int(
                payload.get("lastSummonTreasureResult") or 0
            ),
            llm_resp=str(payload.get("llmResp") or ""),
            last_cmd_result=str(payload.get("lastCmdResult") or ""),
            phase_task=str(payload.get("phaseTask") or ""),
            enemy_roles=tuple(
                Unit.load(role) for role in
                (payload.get("teamEnemy") or {}).get("roles") or ()
            ),
        )

    def station(self) -> Unit | None:
        for unit in self.ours:
            if unit.kind == STATION:
                return unit
        return None

    def alive(self, kinds: tuple[str, ...]) -> tuple[Unit, ...]:
        return tuple(
            unit for unit in self.ours
            if unit.kind in kinds and unit.health > 0
        )

    def controllable(self) -> tuple[Unit, ...]:
        return tuple(sorted(
            self.alive(CONTROLLABLE_TYPES), key=lambda unit: unit.unit_id,
        ))

    def workers(self) -> tuple[Unit, ...]:
        return tuple(sorted(
            self.alive((WORKER,)), key=lambda unit: unit.unit_id,
        ))

    def pioneer(self) -> Unit | None:
        for unit in self.alive((PIONEER,)):
            return unit
        return None

    def towers(self) -> tuple[Unit, ...]:
        return tuple(sorted(
            self.alive(TOWER_TYPES),
            key=lambda unit: (unit.pos.x, unit.pos.y),
        ))

    def walls(self) -> tuple[Unit, ...]:
        return self.alive((WALL,))

    def wall_at(self, pos: Pos) -> Unit | None:
        for unit in self.walls():
            if unit.pos == pos:
                return unit
        return None

    def tower_at(self, pos: Pos) -> Unit | None:
        for unit in self.towers():
            if unit.pos == pos:
                return unit
        return None

    def footprint(self, unit: Unit) -> tuple[Pos, ...]:
        if unit.kind == STATION:
            return station_footprint(unit.pos)
        return (unit.pos,)

    def occupied_cells(self) -> frozenset[Pos]:
        cells: set[Pos] = set()
        for unit in self.ours:
            cells.update(self.footprint(unit))
        return frozenset(cells)

    def enemy_buildings(self) -> frozenset[Pos]:
        cells: set[Pos] = set()
        for unit in self.enemy_roles:
            if unit.kind in (STATION, WALL, *TOWER_TYPES):
                cells.update(self.footprint(unit))
        return frozenset(cells)

    def blocked(
        self,
        moving: Unit,
        *,
        ignore_robots: bool = False,
    ) -> frozenset[Pos]:
        cells = {pos for pos, kind in self.zones.items() if kind not in ("land", "")}
        cells.update(self.occupied_cells())
        cells.update(self.enemy_buildings())
        cells.discard(moving.pos)
        if not ignore_robots:
            for robot in self.robots:
                cells.add(robot.pos)
        return frozenset(cells)

    def land(self, pos: Pos) -> bool:
        if not 0 <= pos.x < self.width or not 0 <= pos.y < self.height:
            return False
        return self.zones.get(pos, "land") == "land"

    def is_weapon_site(self, pos: Pos) -> bool:
        return self.land(pos)

    def resource_cells(self, name: str) -> tuple[Pos, ...]:
        return tuple(
            pos for pos, kind in self.zones.items() if kind == name
        )

    def vendor_pos(self) -> Pos | None:
        for pos, kind in self.zones.items():
            if kind == "vendor":
                return pos
        return None

    def weapon_shop_pos(self) -> Pos | None:
        for pos, kind in self.zones.items():
            if kind == "weaponShop":
                return pos
        return None

    def task_point_positions(self) -> tuple[Pos, ...]:
        return tuple(
            pos for pos, kind in self.zones.items()
            if kind.startswith("challengerTaskPoint") or kind.startswith("defenderTaskPoint")
        )


def move_command(pos: Pos) -> dict[str, Any]:
    return {"action": "move", "targetPos": [pos.dump()]}


def collect_command(pos: Pos) -> dict[str, Any]:
    return {"action": "collect", "targetPos": [pos.dump()]}


def build_command(pos: Pos, name: str) -> dict[str, Any]:
    return {"action": "build", "targetPos": [pos.dump()], "name": name}


def attack_command(controller_id: int, *positions: Pos) -> dict[str, Any]:
    return {
        "action": "attack",
        "targetPos": [p.dump() for p in positions],
        "controllerId": str(controller_id),
    }


def buy_command(name: str, num: int = 1) -> dict[str, Any]:
    return {"action": "buy", "name": name, "num": num}


def sell_command(name: str, num: int = 1) -> dict[str, Any]:
    return {"action": "sell", "name": name, "num": num}


def use_command(name: str, target_pos: Pos | None = None) -> dict[str, Any]:
    cmd: dict[str, Any] = {"action": "use", "name": name}
    if target_pos is not None:
        cmd["targetPos"] = [target_pos.dump()]
    return cmd


def accept_task_command() -> dict[str, Any]:
    return {"action": "acceptTask"}


def submit_answer_command(answer: str) -> dict[str, Any]:
    return {"action": "submitAnswer", "taskAnswer": answer}


def summon_treasure_command(pos: Pos, items: list[str]) -> dict[str, Any]:
    return {"action": "summonTreasure", "targetPos": [pos.dump()], "item": items}


def drop_command(name: str, num: int = 1) -> dict[str, Any]:
    return {"action": "drop", "name": name, "num": num}


def remove_command(pos: Pos) -> dict[str, Any]:
    return {"action": "remove", "targetPos": [pos.dump()]}
