from collections import deque
from dataclasses import asdict, dataclass, field, fields
from random import Random
from string import ascii_uppercase
from typing import NamedTuple

LOG_LIMIT = 400
LIST_LIMIT = 8

WET = "~"
SICK = "!"

STAGE_WORDS = ("a sprout", "small", "half grown", "well grown", "nearly full grown")


class Ground(NamedTuple):
    glyph: str
    look: str


GROUND = {
    "fallow": Ground(".", "bare ground"),
    "tilled": Ground("-", "bare soil"),
    "withered": Ground("x", "a dead plant"),
}

DEFAULT_CROPS = {
    "wheat": {"cost": 5, "hours": 20, "price": 12},
    "corn": {"cost": 12, "hours": 40, "price": 30},
    "tomato": {"cost": 20, "hours": 60, "price": 55},
    "pumpkin": {"cost": 40, "hours": 100, "price": 120},
}


class CommandError(Exception):
    pass


@dataclass
class Config:
    seconds_per_hour: float = 5.0
    seed: int | None = None
    rows: int = 4
    cols: int = 6
    start_money: int = 50
    dawn: int = 6
    dusk: int = 20
    water_hours: int = 12
    thirst_hours: int = 20
    rot_hours: int = 48
    rain_chance: float = 0.04
    rain_stops: float = 0.35
    stages: int = 3
    show_distress: bool = True
    spoil_rules: bool = False
    crops: dict = field(default_factory=lambda: {n: dict(c) for n, c in DEFAULT_CROPS.items()})

    @property
    def labels(self):
        return [
            f"{row}{col}"
            for row in ascii_uppercase[: self.rows]
            for col in range(1, self.cols + 1)
        ]

    @property
    def stage_words(self):
        if self.stages == 1:
            return ("growing",)
        span = len(STAGE_WORDS) - 1
        return tuple(STAGE_WORDS[round(i * span / (self.stages - 1))] for i in range(self.stages))


LIMITS = {
    "seconds_per_hour": (0.05, 3600.0),
    "rows": (1, 12),
    "cols": (1, 12),
    "start_money": (0, 1000000),
    "dawn": (0, 23),
    "dusk": (1, 24),
    "water_hours": (1, 480),
    "thirst_hours": (1, 480),
    "rot_hours": (1, 480),
    "rain_chance": (0.0, 1.0),
    "rain_stops": (0.0, 1.0),
    "stages": (1, len(STAGE_WORDS)),
}

CROP_KEYS = {"cost", "hours", "price"}


def _check_crops(crops):
    if not isinstance(crops, dict) or not crops:
        raise CommandError("crops must be a non-empty object")
    initials = {}
    for name, spec in crops.items():
        if not name.isalpha():
            raise CommandError(f"crop name '{name}' must be letters only")
        if not isinstance(spec, dict) or set(spec) != CROP_KEYS:
            raise CommandError(f"crop '{name}' needs exactly cost, hours and price")
        for key, value in spec.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandError(f"{name}.{key} must be a whole number of 0 or more")
        if not spec["hours"]:
            raise CommandError(f"{name}.hours must be at least 1")
        clash = initials.setdefault(name[0].lower(), name)
        if clash != name:
            raise CommandError(f"'{name}' and '{clash}' share a first letter, so the map is unreadable")


def configure(values):
    known = {f.name: f.type for f in fields(Config)}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise CommandError(f"unknown setting(s): {', '.join(unknown)}")
    merged = {**asdict(Config()), **values}

    for name, (low, high) in LIMITS.items():
        value = merged[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandError(f"{name} must be a number")
        if not low <= value <= high:
            raise CommandError(f"{name} must be between {low} and {high}")
        merged[name] = int(value) if known[name] is int else float(value)

    if merged["dawn"] >= merged["dusk"]:
        raise CommandError("dawn must come before dusk")
    if merged["seed"] is not None and (
        isinstance(merged["seed"], bool) or not isinstance(merged["seed"], int)
    ):
        raise CommandError("seed must be a whole number or null")
    for flag in ("show_distress", "spoil_rules"):
        if not isinstance(merged[flag], bool):
            raise CommandError(f"{flag} must be true or false")
    _check_crops(merged["crops"])
    return Config(**merged)


def rules(cfg):
    board = [
        f"  {name:<8} costs ${c['cost']}, needs {c['hours']}h of growth, sells for ${c['price']}"
        for name, c in cfg.crops.items()
    ]
    return "\n".join(
        [
            f"the clock runs on its own: {cfg.dawn:02d}:00 dawn, {cfg.dusk:02d}:00 dusk",
            "crops grow only in daylight on moist soil, and soil dries only in daylight",
            f"watering gives {cfg.water_hours}h of moisture; rain soaks every tilled plot",
            f"a crop dies after {cfg.thirst_hours}h thirsty",
            f"a ripe crop spoils {cfg.rot_hours}h after it ripens",
            "",
            *board,
        ]
    )


class Plot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tilled = False
        self.crop = ""
        self.growth = 0
        self.moisture = 0
        self.thirst = 0
        self.ripe = 0
        self.dead = False

    @property
    def hours_left(self):
        return max(0, self.cfg.crops[self.crop]["hours"] - self.growth) if self.crop else 0

    @property
    def rots_in(self):
        return max(0, self.cfg.rot_hours - self.ripe)

    @property
    def ready(self):
        return bool(self.crop) and not self.dead and self.hours_left == 0

    @property
    def state(self):
        if self.dead:
            return "withered"
        if self.ready:
            return "ready"
        if self.crop:
            return "growing"
        return "tilled" if self.tilled else "fallow"

    @property
    def stage(self):
        done = self.growth / self.cfg.crops[self.crop]["hours"]
        return min(int(done * self.cfg.stages) + 1, self.cfg.stages)

    @property
    def distressed(self):
        if not self.cfg.show_distress or self.dead or not self.crop:
            return False
        if self.ready:
            return self.ripe * 2 >= self.cfg.rot_hours
        return self.thirst * 2 >= self.cfg.thirst_hours

    @property
    def appearance(self):
        if self.state in GROUND:
            return GROUND[self.state].look
        if self.ready:
            return "ripe, and starting to turn" if self.distressed else "ripe"
        return self.cfg.stage_words[self.stage - 1] + (", and wilting" if self.distressed else "")

    def empty(self):
        self.crop, self.growth, self.thirst, self.ripe, self.dead = "", 0, 0, 0, False


PLOT_FIELDS = ("tilled", "crop", "growth", "moisture", "thirst", "ripe", "dead")


def _dump_plot(plot):
    return {name: getattr(plot, name) for name in PLOT_FIELDS}


def _load_plot(cfg, data):
    plot = Plot(cfg)
    for name in PLOT_FIELDS:
        setattr(plot, name, data[name])
    return plot


class Farm:
    def __init__(self, config=None):
        self.cfg = config or Config()
        self.revision = 0
        self.reset()

    def reset(self):
        self.revision += 1
        self.rng = Random(self.cfg.seed)
        self.hour = self.cfg.dawn
        self.raining = False
        self.money = self.cfg.start_money
        self.plots = {label: Plot(self.cfg) for label in self.cfg.labels}
        self.inventory = {}
        self.log = deque(maxlen=LOG_LIMIT)
        self.cursor = 0

    @property
    def day(self):
        return self.hour // 24 + 1

    @property
    def clock(self):
        return f"{self.hour % 24:02d}:00"

    @property
    def daylight(self):
        return self.cfg.dawn <= self.hour % 24 < self.cfg.dusk

    @property
    def phase(self):
        return "daylight" if self.daylight else "night"

    @property
    def weather(self):
        return "rain" if self.raining else "clear"


def dump(farm):
    version, internal_state, gauss_next = farm.rng.getstate()
    return {
        "cfg": asdict(farm.cfg),
        "revision": farm.revision,
        "rng_state": [version, internal_state, gauss_next],
        "hour": farm.hour,
        "raining": farm.raining,
        "money": farm.money,
        "plots": {label: _dump_plot(plot) for label, plot in farm.plots.items()},
        "inventory": farm.inventory,
        "log": list(farm.log),
        "cursor": farm.cursor,
    }


def load(data):
    farm = Farm(Config(**data["cfg"]))
    version, internal_state, gauss_next = data["rng_state"]
    farm.rng.setstate((version, tuple(internal_state), gauss_next))
    farm.revision = data["revision"]
    farm.hour = data["hour"]
    farm.raining = data["raining"]
    farm.money = data["money"]
    farm.plots = {label: _load_plot(farm.cfg, plot) for label, plot in data["plots"].items()}
    farm.inventory = data["inventory"]
    farm.log = deque(data["log"], maxlen=LOG_LIMIT)
    farm.cursor = data["cursor"]
    return farm


def _note(farm, text):
    farm.cursor += 1
    farm.log.append({"id": farm.cursor, "day": farm.day, "time": farm.clock, "text": text})


def since(farm, cursor):
    return [event for event in farm.log if event["id"] > cursor]


def tick(farm):
    cfg = farm.cfg
    farm.hour += 1
    farm.revision += 1
    hour_of_day = farm.hour % 24
    if hour_of_day == cfg.dawn:
        _note(farm, "the sun rises")
    elif hour_of_day == cfg.dusk:
        _note(farm, "the sun sets")

    was_raining = farm.raining
    farm.raining = (
        farm.rng.random() >= cfg.rain_stops if was_raining else farm.rng.random() < cfg.rain_chance
    )
    if farm.raining != was_raining:
        _note(farm, "rain begins to fall" if farm.raining else "the rain stops")

    ripened, withered, rotted = [], [], []
    for label, plot in farm.plots.items():
        if farm.raining and plot.tilled:
            plot.moisture = cfg.water_hours
        if plot.dead or not plot.crop:
            continue
        if plot.ready:
            plot.ripe += 1
            if not plot.rots_in:
                plot.dead = True
                rotted.append(label)
        elif farm.daylight:
            if plot.moisture:
                plot.growth += 1
                plot.thirst = 0
                if plot.ready:
                    ripened.append(label)
            else:
                plot.thirst += 1
                if plot.thirst >= cfg.thirst_hours:
                    plot.dead = True
                    withered.append(label)
        if farm.daylight and plot.moisture:
            plot.moisture -= 1

    for labels, text in (
        (ripened, "turned ripe"),
        (withered, "shrivelled up"),
        (rotted, "went bad"),
    ):
        if labels:
            _note(farm, f"{_listed(labels)} {text}")


def _coord(farm, token):
    rows = ascii_uppercase[: farm.cfg.rows]
    label = token.upper()
    if len(label) < 2 or label[0] not in rows or not label[1:].isdigit():
        raise CommandError(f"bad plot '{token}' (use A1..{rows[-1]}{farm.cfg.cols})")
    col = int(label[1:])
    if not 1 <= col <= farm.cfg.cols:
        raise CommandError(f"bad plot '{token}' (columns 1..{farm.cfg.cols})")
    return rows.index(label[0]), col


def _targets(farm, tokens):
    if not tokens:
        raise CommandError("specify plots, e.g. a1, a1-b3, all")
    rows = ascii_uppercase[: farm.cfg.rows]
    labels = []
    for token in tokens:
        if token.lower() == "all":
            labels += farm.cfg.labels
        elif "-" in token:
            start, end = token.split("-", 1)
            (row_a, col_a), (row_b, col_b) = _coord(farm, start), _coord(farm, end)
            labels += [
                f"{rows[row]}{col}"
                for row in range(min(row_a, row_b), max(row_a, row_b) + 1)
                for col in range(min(col_a, col_b), max(col_a, col_b) + 1)
            ]
        else:
            row, col = _coord(farm, token)
            labels.append(f"{rows[row]}{col}")
    return list(dict.fromkeys(labels))


def _number(token, low, high):
    if not token.isdigit() or not low <= int(token) <= high:
        raise CommandError(f"'{token}' must be a number from {low} to {high}")
    return int(token)


def _listed(labels):
    shown = ", ".join(labels[:LIST_LIMIT])
    extra = len(labels) - LIST_LIMIT
    return f"{shown} +{extra} more" if extra > 0 else shown


def _apply(farm, tokens, verb, action):
    done, skipped = [], {}
    for label in _targets(farm, tokens):
        reason = action(farm, farm.plots[label])
        if reason:
            skipped.setdefault(reason, []).append(label)
        else:
            done.append(label)
    farm.revision += bool(done)
    report = [f"{verb} {_listed(done)}"] if done else []
    report += [f"skipped {_listed(labels)} ({reason})" for reason, labels in skipped.items()]
    return "; ".join(report)


def _till(farm, plot):
    if plot.crop or plot.dead:
        return "occupied"
    if plot.tilled:
        return "already tilled"
    plot.tilled = True


def _plant(farm, plot, name):
    if plot.dead:
        return "a dead plant is in the way"
    if plot.crop:
        return f"has {plot.crop}"
    if not plot.tilled:
        return "not tilled"
    if farm.money < farm.cfg.crops[name]["cost"]:
        return "no funds"
    farm.money -= farm.cfg.crops[name]["cost"]
    plot.crop, plot.growth, plot.thirst, plot.ripe = name, 0, 0, 0


def _water(farm, plot):
    if not plot.tilled:
        return "not tilled"
    plot.moisture = farm.cfg.water_hours
    plot.thirst = 0


def _harvest(farm, plot):
    if not plot.crop:
        return "nothing planted"
    if plot.dead:
        return "a dead plant is in the way"
    if not plot.ready:
        return "not ripe yet"
    farm.inventory[plot.crop] = farm.inventory.get(plot.crop, 0) + 1
    plot.empty()


def _clear(farm, plot):
    if not plot.dead:
        return "nothing to clear"
    plot.empty()


def cmd_till(farm, tokens):
    return _apply(farm, tokens, "tilled", _till)


def cmd_plant(farm, tokens):
    crops = farm.cfg.crops
    if not tokens:
        raise CommandError(f"plant <crop> <plots> — crops: {', '.join(crops)}")
    name = tokens[0].lower()
    if name not in crops:
        raise CommandError(f"unknown crop '{tokens[0]}' — try {', '.join(crops)}")
    return _apply(farm, tokens[1:], f"planted {name} in", lambda f, p: _plant(f, p, name))


def cmd_water(farm, tokens):
    return _apply(farm, tokens, "watered", _water)


def cmd_harvest(farm, tokens):
    return _apply(farm, tokens, "harvested", _harvest)


def cmd_clear(farm, tokens):
    return _apply(farm, tokens, "cleared", _clear)


def cmd_sell(farm, tokens):
    crops = farm.cfg.crops
    if not tokens:
        raise CommandError("sell <crop|all> [qty]")
    name = tokens[0].lower()
    if name != "all" and name not in crops:
        raise CommandError(f"unknown crop '{tokens[0]}' — try {', '.join(crops)}")
    wanted = _number(tokens[1], 1, 999) if len(tokens) > 1 else None
    names = list(farm.inventory) if name == "all" else [name]
    sold, earned = [], 0
    for crop_name in names:
        count = farm.inventory.get(crop_name, 0)
        count = count if wanted is None else min(wanted, count)
        if count <= 0:
            continue
        earned += count * crops[crop_name]["price"]
        farm.inventory[crop_name] -= count
        if not farm.inventory[crop_name]:
            del farm.inventory[crop_name]
        sold.append(f"{count} {crop_name}")
    if not sold:
        raise CommandError("barn is empty" if name == "all" else f"no {name} in the barn")
    farm.money += earned
    farm.revision += 1
    return f"sold {', '.join(sold)} for ${earned}"


def cmd_log(farm, tokens):
    count = _number(tokens[0], 1, LOG_LIMIT) if tokens else 15
    recent = list(farm.log)[-count:]
    if not recent:
        return "nothing has happened yet"
    return "\n".join(f"  d{e['day']} {e['time']}  {e['text']}" for e in recent)


def cmd_look(farm, tokens):
    return render(farm, farm.cfg.spoil_rules)


def cmd_help(farm, tokens):
    cfg = farm.cfg
    board = [
        f"  {name:<8} costs ${c['cost']:<4} sells for ${c['price']}"
        for name, c in cfg.crops.items()
    ]
    ground = [f"  {plot.glyph:<4} {plot.look}" for plot in GROUND.values()]
    example = next(iter(cfg.crops))
    sample = example[0]
    sizes = [
        f"  {sample}{n:<3} a {example} plant, {word}"
        for n, word in enumerate(cfg.stage_words, 1)
    ]
    text = [
        "commands (plots: a1, a1-b3, all)",
        "  look                 look at the farm",
        "  till <plots>         turn the soil",
        "  plant <crop> <plots> sow seeds",
        "  water <plots>        pour water on the soil",
        "  harvest <plots>      pick what is ripe",
        "  clear <plots>        pull out a dead plant",
        "  sell <crop|all> [n]  sell what is in the barn",
        "  log [n]              what you noticed recently",
        "",
        "seed board",
        *board,
        "",
        "what you see on the map",
        *ground,
        f"  {WET:<4} the soil looks damp",
        *(sizes if cfg.stages > 1 else []),
        f"  {sample.upper():<4} a {example} plant that looks ripe",
    ]
    if cfg.show_distress:
        text.append(f"  {SICK:<4} this plant does not look healthy")
    if cfg.spoil_rules:
        text += ["", "how the farm works", rules(cfg)]
    return "\n".join(text)


COMMANDS = {
    "look": cmd_look,
    "status": cmd_look,
    "till": cmd_till,
    "plant": cmd_plant,
    "water": cmd_water,
    "harvest": cmd_harvest,
    "clear": cmd_clear,
    "sell": cmd_sell,
    "log": cmd_log,
    "help": cmd_help,
}


def cell(plot, detail=False):
    if plot.state in GROUND:
        base = GROUND[plot.state].glyph
    elif plot.ready:
        base = plot.crop[0].upper() + (str(plot.rots_in) if detail else "")
    elif detail:
        base = plot.crop[0] + str(plot.hours_left)
    else:
        base = plot.crop[0] + (str(plot.stage) if plot.cfg.stages > 1 else "")
    if detail:
        warning = f"{SICK}{plot.thirst}" if plot.thirst else ""
    else:
        warning = SICK if plot.distressed else ""
    return base + (WET if plot.moisture else "") + warning


def render(farm, detail=False):
    width = 8 if detail else 6
    rows = ascii_uppercase[: farm.cfg.rows]

    def line(label, cells):
        return (label.ljust(2) + "".join(f"{text:<{width}}" for text in cells)).rstrip()

    grid = [line("", [str(col) for col in range(1, farm.cfg.cols + 1)])]
    grid += [
        line(row, [cell(farm.plots[f"{row}{col}"], detail) for col in range(1, farm.cfg.cols + 1)])
        for row in rows
    ]
    barn = ", ".join(f"{name} x{count}" for name, count in farm.inventory.items()) or "empty"
    header = f"Day {farm.day}  {farm.clock} {farm.phase}, {farm.weather}  ${farm.money}"
    return "\n".join([header, "", *grid, "", f"Barn: {barn}"])


def _seen(plot, detail):
    if not detail:
        return {"crop": plot.crop, "looks": plot.appearance, "damp": bool(plot.moisture)}
    return {
        "crop": plot.crop,
        "state": plot.state,
        "hours_left": plot.hours_left,
        "rots_in": plot.rots_in if plot.ready else None,
        "moisture": plot.moisture,
        "thirst": plot.thirst,
    }


def snapshot(farm, detail=False):
    detail = detail or farm.cfg.spoil_rules
    crops = {
        name: dict(spec) if detail else {"cost": spec["cost"], "price": spec["price"]}
        for name, spec in farm.cfg.crops.items()
    }
    return {
        "day": farm.day,
        "time": farm.clock,
        "phase": farm.phase,
        "weather": farm.weather,
        "money": farm.money,
        "barn": farm.inventory,
        "crops": crops,
        "plots": {label: _seen(plot, detail) for label, plot in farm.plots.items()},
        "ascii": render(farm, detail),
        "cursor": farm.cursor,
        "revision": farm.revision,
    }


def run(farm, line):
    tokens = line.split()
    if not tokens:
        return True, ""
    handler = COMMANDS.get(tokens[0].lower())
    if not handler:
        return False, f"unknown command '{tokens[0]}' — try help"
    revision = farm.revision
    try:
        message = handler(farm, tokens[1:])
    except CommandError as error:
        return False, str(error)
    if message and farm.revision != revision:
        _note(farm, message)
    return True, message
