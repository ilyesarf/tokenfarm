from collections import deque
from random import Random
from typing import NamedTuple

ROWS = "ABCD"
COLS = 6
START_MONEY = 50

HOURS_PER_DAY = 24
DAWN, DUSK = 6, 20
WATER_HOURS = 12
THIRST_LIMIT = 20
ROT_HOURS = 48
RAIN_START = 0.04
RAIN_STOP = 0.35

LOG_LIMIT = 400
LIST_LIMIT = 8

WET = "~"
SICK = "!"
STAGES = ("a sprout", "half grown", "nearly full grown")


class Ground(NamedTuple):
    glyph: str
    look: str


GROUND = {
    "fallow": Ground(".", "bare ground"),
    "tilled": Ground("-", "bare soil"),
    "withered": Ground("x", "a dead plant"),
}


class Crop(NamedTuple):
    cost: int
    hours: int
    price: int


CROPS = {
    "wheat": Crop(cost=5, hours=20, price=12),
    "corn": Crop(cost=12, hours=40, price=30),
    "tomato": Crop(cost=20, hours=60, price=55),
    "pumpkin": Crop(cost=40, hours=100, price=120),
}

LABELS = [f"{row}{col}" for row in ROWS for col in range(1, COLS + 1)]


class CommandError(Exception):
    pass


class Plot:
    def __init__(self):
        self.tilled = False
        self.crop = ""
        self.growth = 0
        self.moisture = 0
        self.thirst = 0
        self.ripe = 0
        self.dead = False

    @property
    def hours_left(self):
        return max(0, CROPS[self.crop].hours - self.growth) if self.crop else 0

    @property
    def rots_in(self):
        return max(0, ROT_HOURS - self.ripe)

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
        done = self.growth / CROPS[self.crop].hours
        return min(int(done * len(STAGES)) + 1, len(STAGES))

    @property
    def distressed(self):
        if self.ready:
            return self.ripe * 2 >= ROT_HOURS
        return bool(self.crop) and not self.dead and self.thirst * 2 >= THIRST_LIMIT

    @property
    def appearance(self):
        if self.state in GROUND:
            return GROUND[self.state].look
        if self.ready:
            return "ripe, and starting to turn" if self.distressed else "ripe"
        return STAGES[self.stage - 1] + (", and wilting" if self.distressed else "")

    def empty(self):
        self.crop, self.growth, self.thirst, self.ripe, self.dead = "", 0, 0, 0, False


class Farm:
    def __init__(self, seed=None):
        self.seed = seed
        self.revision = 0
        self.reset()

    def reset(self):
        self.revision += 1
        self.rng = Random(self.seed)
        self.hour = DAWN
        self.raining = False
        self.money = START_MONEY
        self.plots = {label: Plot() for label in LABELS}
        self.inventory = {}
        self.log = deque(maxlen=LOG_LIMIT)
        self.cursor = 0

    @property
    def day(self):
        return self.hour // HOURS_PER_DAY + 1

    @property
    def clock(self):
        return f"{self.hour % HOURS_PER_DAY:02d}:00"

    @property
    def daylight(self):
        return DAWN <= self.hour % HOURS_PER_DAY < DUSK

    @property
    def phase(self):
        return "daylight" if self.daylight else "night"

    @property
    def weather(self):
        return "rain" if self.raining else "clear"


def _note(farm, text):
    farm.cursor += 1
    farm.log.append({"id": farm.cursor, "day": farm.day, "time": farm.clock, "text": text})


def since(farm, cursor):
    return [event for event in farm.log if event["id"] > cursor]


def tick(farm):
    farm.hour += 1
    farm.revision += 1
    hour_of_day = farm.hour % HOURS_PER_DAY
    if hour_of_day == DAWN:
        _note(farm, "the sun rises")
    elif hour_of_day == DUSK:
        _note(farm, "the sun sets")

    was_raining = farm.raining
    farm.raining = (
        farm.rng.random() >= RAIN_STOP if was_raining else farm.rng.random() < RAIN_START
    )
    if farm.raining != was_raining:
        _note(farm, "rain begins to fall" if farm.raining else "the rain stops")

    ripened, withered, rotted = [], [], []
    for label, plot in farm.plots.items():
        if farm.raining and plot.tilled:
            plot.moisture = WATER_HOURS
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
                if plot.thirst >= THIRST_LIMIT:
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


def _coord(token):
    label = token.upper()
    if len(label) < 2 or label[0] not in ROWS or not label[1:].isdigit():
        raise CommandError(f"bad plot '{token}' (use A1..{ROWS[-1]}{COLS})")
    col = int(label[1:])
    if not 1 <= col <= COLS:
        raise CommandError(f"bad plot '{token}' (columns 1..{COLS})")
    return ROWS.index(label[0]), col


def _targets(tokens):
    if not tokens:
        raise CommandError("specify plots, e.g. a1, a1-b3, all")
    labels = []
    for token in tokens:
        if token.lower() == "all":
            labels += LABELS
        elif "-" in token:
            start, end = token.split("-", 1)
            (row_a, col_a), (row_b, col_b) = _coord(start), _coord(end)
            labels += [
                f"{ROWS[row]}{col}"
                for row in range(min(row_a, row_b), max(row_a, row_b) + 1)
                for col in range(min(col_a, col_b), max(col_a, col_b) + 1)
            ]
        else:
            row, col = _coord(token)
            labels.append(f"{ROWS[row]}{col}")
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
    for label in _targets(tokens):
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
    if farm.money < CROPS[name].cost:
        return "no funds"
    farm.money -= CROPS[name].cost
    plot.crop, plot.growth, plot.thirst, plot.ripe = name, 0, 0, 0


def _water(farm, plot):
    if not plot.tilled:
        return "not tilled"
    plot.moisture = WATER_HOURS
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
    if not tokens:
        raise CommandError(f"plant <crop> <plots> — crops: {', '.join(CROPS)}")
    name = tokens[0].lower()
    if name not in CROPS:
        raise CommandError(f"unknown crop '{tokens[0]}' — try {', '.join(CROPS)}")
    return _apply(farm, tokens[1:], f"planted {name} in", lambda f, p: _plant(f, p, name))


def cmd_water(farm, tokens):
    return _apply(farm, tokens, "watered", _water)


def cmd_harvest(farm, tokens):
    return _apply(farm, tokens, "harvested", _harvest)


def cmd_clear(farm, tokens):
    return _apply(farm, tokens, "cleared", _clear)


def cmd_sell(farm, tokens):
    if not tokens:
        raise CommandError("sell <crop|all> [qty]")
    name = tokens[0].lower()
    if name != "all" and name not in CROPS:
        raise CommandError(f"unknown crop '{tokens[0]}' — try {', '.join(CROPS)}")
    wanted = _number(tokens[1], 1, 999) if len(tokens) > 1 else None
    names = list(farm.inventory) if name == "all" else [name]
    sold, earned = [], 0
    for crop_name in names:
        count = farm.inventory.get(crop_name, 0)
        count = count if wanted is None else min(wanted, count)
        if count <= 0:
            continue
        earned += count * CROPS[crop_name].price
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
    return render(farm)


def cmd_reset(farm, tokens):
    farm.reset()
    return "farm reset"


def cmd_help(farm, tokens):
    board = [
        f"  {name:<8} costs ${crop.cost:<4} sells for ${crop.price}"
        for name, crop in CROPS.items()
    ]
    ground = [f"  {plot.glyph:<4} {plot.look}" for plot in GROUND.values()]
    return "\n".join(
        [
            "commands (plots: a1, a1-b3, all)",
            "  look                 look at the farm",
            "  till <plots>         turn the soil",
            "  plant <crop> <plots> sow seeds",
            "  water <plots>        pour water on the soil",
            "  harvest <plots>      pick what is ripe",
            "  clear <plots>        pull out a dead plant",
            "  sell <crop|all> [n]  sell what is in the barn",
            "  log [n]              what you noticed recently",
            "  reset                start over",
            "",
            "seed board",
            *board,
            "",
            "what you see on the map",
            *ground,
            f"  {WET:<4} the soil looks damp",
            "  w1   a wheat plant, just a sprout",
            "  w3   a wheat plant, nearly full grown",
            "  W    a wheat plant that looks ripe",
            f"  {SICK:<4} this plant does not look healthy",
        ]
    )


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
    "reset": cmd_reset,
    "help": cmd_help,
}

def cell(plot, detail=False):
    if plot.state in GROUND:
        base = GROUND[plot.state].glyph
    elif plot.ready:
        base = plot.crop[0].upper() + (str(plot.rots_in) if detail else "")
    else:
        base = plot.crop[0] + str(plot.hours_left if detail else plot.stage)
    if detail:
        warning = f"{SICK}{plot.thirst}" if plot.thirst else ""
    else:
        warning = SICK if plot.distressed else ""
    return base + (WET if plot.moisture else "") + warning


def render(farm, detail=False):
    width = 8 if detail else 6

    def line(label, cells):
        return (label.ljust(2) + "".join(f"{text:<{width}}" for text in cells)).rstrip()

    grid = [line("", [str(col) for col in range(1, COLS + 1)])]
    grid += [
        line(row, [cell(farm.plots[f"{row}{col}"], detail) for col in range(1, COLS + 1)])
        for row in ROWS
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
    crops = {
        name: crop._asdict() if detail else {"cost": crop.cost, "price": crop.price}
        for name, crop in CROPS.items()
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
    name = tokens[0].lower()
    handler = COMMANDS.get(name)
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
