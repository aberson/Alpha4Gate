"""Camera priorities and shot pacing must not depend on Win32 or gameplay writes."""

from jev.camera import CameraDirector, CameraEntity, CameraSubject, camera_subjects
from jev.contracts import Task


def test_contact_interrupts_a_quiet_shot_but_not_for_routine_construction():
    director = CameraDirector()
    home = CameraSubject("home", (0, 0), 10, "Home")
    build = CameraSubject("build", (5, 5), 60, "Build")
    fight = CameraSubject("fight", (20, 20), 100, "Fight")
    assert director.choose(0, (home,)) == home
    assert director.choose(1, (home, build)) == home
    assert director.choose(2, (home, build, fight)) == fight


def test_quiet_shots_rotate_and_disappearing_subjects_release_immediately():
    director = CameraDirector()
    build = CameraSubject("build", (5, 5), 60, "Build")
    scout = CameraSubject("scout", (20, 20), 55, "Scout")
    assert director.choose(0, (build, scout)) == build
    assert director.choose(12, (build, scout)) == scout
    assert director.choose(13, (build,)) == build


def test_following_updates_position_without_restarting_dwell():
    director = CameraDirector()
    first = CameraSubject("scout", (1, 1), 55, "Scout")
    moved = CameraSubject("scout", (2, 2), 55, "Scout")
    director.choose(0, (first,))
    assert director.choose(2, (moved,)) == moved
    assert director.since == 0


def test_candidates_use_own_positions_and_only_supplied_visible_contacts():
    own = (
        CameraEntity(1, (10, 10), structure=True, ready=False),
        CameraEntity(2, (80, 80), attacking=True),
    )
    quiet = camera_subjects(own, (), (), (1, 1))
    assert all(item.priority < 100 for item in quiet)
    contact = camera_subjects(own, ((11, 10),), (), (1, 1))
    fight = next(item for item in contact if item.priority == 100)
    assert fight.position == (10, 10)
    assert {item.reason for item in quiet} == {
        "Home base",
        "Construction in progress",
        "Army advancing",
    }


def test_scout_follows_its_current_actor_not_the_distant_task_destination():
    task = Task("t", "scouting.move", "scout", 42, (99, 99), "running", 0, 30, 1, 1, "moving")
    subjects = camera_subjects((CameraEntity(42, (12, 14)),), (), (task,), (0, 0))
    scout = next(item for item in subjects if item.reason == "Scouting")
    assert scout.position == (12, 14)
    assert all(item.reason != "Scouting" for item in camera_subjects((), (), (task,), (0, 0)))
