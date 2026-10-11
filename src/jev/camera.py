"""Presentation-only camera director; never issues unit orders or queries hidden units."""

from dataclasses import dataclass
from math import dist

from jev.contracts import Task

Point = tuple[float, float]


@dataclass(frozen=True)
class CameraSubject:
    key: str
    position: Point
    priority: int
    reason: str


@dataclass(frozen=True)
class CameraEntity:
    tag: int
    position: Point
    structure: bool = False
    ready: bool = True
    attacking: bool = False


def camera_subjects(
    own: tuple[CameraEntity, ...],
    visible_enemies: tuple[Point, ...],
    tasks: tuple[Task, ...],
    home: Point,
) -> tuple[CameraSubject, ...]:
    subjects = [CameraSubject("home", home, 10, "Home base")]
    by_tag = {entity.tag: entity for entity in own}
    for entity in own:
        key = str(entity.tag)
        if any(dist(entity.position, enemy) <= 12 for enemy in visible_enemies):
            subjects.append(CameraSubject(key, entity.position, 100, "Visible enemy contact"))
        elif entity.attacking and not entity.structure:
            subjects.append(CameraSubject(key, entity.position, 80, "Army advancing"))
        elif entity.structure and not entity.ready:
            subjects.append(CameraSubject(key, entity.position, 60, "Construction in progress"))
    for task in tasks:
        actor = by_tag.get(task.actor_tag) if task.actor_tag is not None else None
        if actor and task.node_id.startswith("scouting."):
            subjects.append(CameraSubject(f"scout:{actor.tag}", actor.position, 55, "Scouting"))
    return tuple(subjects)


class CameraDirector:
    """Hold shots for five game seconds; rotate quieter subjects after twelve."""

    def __init__(self) -> None:
        self.subject: CameraSubject | None = None
        self.since = 0.0

    def choose(self, now: float, candidates: tuple[CameraSubject, ...]) -> CameraSubject:
        if not candidates:
            raise ValueError("Camera requires at least a home subject")
        ranked = sorted(candidates, key=lambda item: (-item.priority, item.key))
        current = next(
            (item for item in ranked if self.subject and item.key == self.subject.key), None
        )
        chosen = ranked[0]
        if current:
            urgent = chosen.priority == 100 and current.priority < 100
            if not urgent and now - self.since < 5:
                chosen = current
            elif now - self.since >= 12 and current.priority < 100:
                chosen = next(
                    (
                        item
                        for item in ranked
                        if item.key != current.key and item.priority >= current.priority - 20
                    ),
                    chosen,
                )
        if self.subject is None or chosen.key != self.subject.key:
            self.since = now
        self.subject = chosen
        return chosen
