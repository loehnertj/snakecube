"""Program to solve snake cube"""

import dataclasses as dc
from threading import Event, Thread
from typing import Callable, Literal, cast
import numpy as np
import pyvista as pv
import time

PLOT_INTERVAL_MS = 100


@dc.dataclass(frozen=True)
class Snakecube:
    size: int
    """Kantenlänge"""
    segments: list[int]
    """Länge der einzelnen Segmente (#Würfel nach denen wieder 90°-Kehre
    kommt.)
    """

    @classmethod
    def from_segment_str(cls, s: str):
        segments = [int(c) for c in s]
        ll = sum(segments)
        if ll == 8:
            size = 2
        elif ll == 27:
            size = 3
        elif ll == 64:
            size = 4
        elif ll == 125:
            size = 5
        else:
            raise ValueError(
                "Summe der Abschnittslängen falsch: erwartet 8, 27, 64 oder 125"
            )
        return cls(size, segments)
    
    @property
    def segment_str(self):
        return "".join(str(s) for s in self.segments)


cube_3 = Snakecube.from_segment_str("31121211221112222")

cube_4 = Snakecube.from_segment_str("2311111111121311132111112211111111212121311212")

# Lösung gefunden:
#   0 B L B  F B L F
#   L F B L  B B R B
#   L L R L  F B F B
#   B R R L  B F F B
#   L R L B  L B R B
#   B L B F  B


AxisDirection = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
TurnDirection = Literal["F", "B", "L", "R", "0"]
"""Relative direction of next turn.

Defined in relation to the previous turn, ie. the direction of the *two*
preceding segments.

Imagine the second-last segment going "Forward" and looking *down* on the
previous turn, the values mean:

* "F": next segment continues in forward direction (same as second-last segment)
* "B": next segment continues opposite to forward direction (U-Turn)
* "L": next segment continues to the left 
* "R": next segment continues to the right
* "0": for first turn (i.e. undefined)

"""
Pos = tuple[int, int, int]


@dc.dataclass(frozen=True)
class PartialSolution:
    """Angefangene Lösung des Würfels"""

    size: int
    """Kantenlänge des Zielwürfels"""
    remaining_segments: list[int]
    """Noch zu platzierende Segmente"""
    occupied: np.ndarray
    """Besetzte Zellen (0 = unbesetzt, 1+ = Nr. des Segments)
    
    3 Indices (x,y,z) jeweils von -size ...size besetzbar
    """
    xbounds: tuple[int, int]
    """slice index of currently occupied cells
    
    E.g. if x=0 and x=1 are occupied, xbounds is (0, 2).
    """
    ybounds: tuple[int, int]
    zbounds: tuple[int, int]
    segment_directions: list[AxisDirection]
    """axis directions of already placed segments.
    
    The first (inital) segment always uses +x.
    """
    turn_directions: list[TurnDirection]
    """
    Turns to take in relative-direction notation; one less element than
    segment directions.
    """
    cursor: Pos
    """Position of last placed cube"""

    @classmethod
    def init_for(cls, cube: Snakecube):
        N = cube.size
        n0 = cube.segments[0]
        # init occupied array
        # indices can go from -(N-1) ... 0 ... N-1.
        occupied = np.zeros((2 * N, 2 * N, 2 * N), dtype=np.uint8)
        for x in range(n0):
            occupied[x, 0, 0] = 1
        return PartialSolution(
            size=N,
            remaining_segments=cube.segments[1:],
            occupied=occupied,
            xbounds=(0, n0),
            ybounds=(0, 1),
            zbounds=(0, 1),
            segment_directions=["+x"],
            turn_directions=[],
            cursor=(n0 - 1, 0, 0),
        )

    def __len__(self):
        return len(self.segment_directions)

    def __str__(self):
        turns = " ".join(self.turn_directions)
        n_solved = len(self.segment_directions)
        n_total = n_solved + len(self.remaining_segments)
        if self.remaining_segments:
            return f"{turns} ({n_solved}/{n_total})"
        else:
            return f"{turns}"

    @property
    def is_solved(self):
        return not bool(self.remaining_segments)


ReportCB = Callable[[PartialSolution, PartialSolution], bool]
"""progress report. Parameters = (current solution, best solution so far)

Return value = whether to go on
"""


def extend(
    ps: PartialSolution, direction: AxisDirection, turn_direction: TurnDirection
) -> PartialSolution | None:
    """try to append the next segment using the given direction.

    Returns None if the new partial solution is invalid:
        * new segment would stretch one of the bounds beyond size, or
        * new segment would reoccupy already-occupied cell.

    ``direction`` gives the direction to use for the new segment.
    ``rel_direction`` specifies what kind of turn was taken.
    """
    x0, y0, z0 = ps.cursor
    xbounds = ps.xbounds
    ybounds = ps.ybounds
    zbounds = ps.zbounds

    signtxt, axis = direction
    sign = -1 if signtxt == "-" else +1
    length = ps.remaining_segments[0]
    # Bounds update + check
    if axis == "x":
        new_x = x0 + sign * length
        xbounds = (min(xbounds[0], new_x), max(xbounds[1], new_x + 1))
        if xbounds[1] - xbounds[0] > ps.size:
            return None
    elif axis == "y":
        new_y = y0 + sign * length
        ybounds = (min(ybounds[0], new_y), max(ybounds[1], new_y + 1))
        if ybounds[1] - ybounds[0] > ps.size:
            return None
    elif axis == "z":
        new_z = z0 + sign * length
        zbounds = (min(zbounds[0], new_z), max(zbounds[1], new_z + 1))
        if zbounds[1] - zbounds[0] > ps.size:
            return None
    else:
        raise ValueError("invalid axis {axis}")

    # Occupancy update+check
    occupied = ps.occupied.copy()
    idx = len(ps.segment_directions) + 1
    sgx = sign if axis == "x" else 0
    sgy = sign if axis == "y" else 0
    sgz = sign if axis == "z" else 0
    for n in range(1, length + 1):
        x, y, z = x0 + sgx * n, y0 + sgy * n, z0 + sgz * n
        if occupied[x, y, z] > 0:
            # Self-intersection
            return None
        occupied[x, y, z] = idx

    # Valid. Return extended solution.
    return PartialSolution(
        size=ps.size,
        remaining_segments=ps.remaining_segments[1:],
        occupied=occupied,
        xbounds=xbounds,
        ybounds=ybounds,
        zbounds=zbounds,
        segment_directions=ps.segment_directions + [direction],
        turn_directions=ps.turn_directions + [turn_direction],
        cursor=(x0 + sgx * length, y0 + sgy * length, z0 + sgz * length),
    )


def continuations(
    direction1: AxisDirection, direction2: AxisDirection
) -> tuple[list[AxisDirection], list[TurnDirection]]:
    """
    Possible continuations when the previous two segments had direction1 and direction2, respectivel.

    Returns (directions, rel_directions), each containing 4 elements.
    """
    rel_directions = cast(list[TurnDirection], ["F", "L", "B", "R"])
    # directions are always in CCW order but not yet starting with the correct one (Forward)
    directions = cast(
        dict[AxisDirection, list[AxisDirection]],
        {
            "+x": ["+y", "-z", "-y", "+z"],
            "-x": ["+y", "+z", "-y", "-z"],
            "+y": ["+x", "+z", "-x", "-z"],
            "-y": ["+x", "-z", "-x", "+z"],
            "+z": ["+x", "-y", "-x", "+y"],
            "-z": ["+x", "+y", "-x", "-y"],
        },
    )[direction2]
    # Rollover to match rel_directions
    idx_F = directions.index(direction1)
    if idx_F != 0:
        directions = directions[idx_F:4] + directions[0:idx_F]
    return directions, rel_directions


def solve_recurse(
    ps: PartialSolution,
    directions: list[AxisDirection],
    turn_directions: list[TurnDirection],
    start_from: str,
    longest_overall: PartialSolution,
    report: ReportCB,
) -> PartialSolution:
    """Solve cube by recursively extending until full solution is found.

    directions gives the possible axis directions for the next extension.

    If start_from is nonempty, skip forward to rel_direction given by first
    letter and pass the remaining string to subsolvers.

    Returns the longest solution that was found.
    """
    longest_solution_here = ps
    skip_to = start_from[0] if start_from else ""
    start_from = start_from[1:]
    for direction, turn_direction in zip(directions, turn_directions):
        if skip_to and turn_direction != skip_to:
            continue
        skip_to = ""
        new_solution = extend(ps, direction, turn_direction)
        if not new_solution:
            # Dead end
            report(ps, longest_overall)
            continue
        elif not new_solution.remaining_segments:
            # Got it!
            report(new_solution, new_solution)
            return new_solution
        else:
            # Search next
            next_directions, next_rel_rel_directions = continuations(
                *new_solution.segment_directions[-2:]
            )
            subsolution = solve_recurse(
                new_solution,
                next_directions,
                next_rel_rel_directions,
                start_from,
                longest_overall=longest_overall,
                report=report,
            )
            # Following recursions search full (no start-skip!)
            start_from = ""
            if len(subsolution) > len(longest_solution_here):
                longest_solution_here = subsolution
            if len(subsolution) > len(longest_overall):
                longest_overall = subsolution
            if subsolution.is_solved:
                return subsolution
    return longest_solution_here


def print_report_factory():
    dead_ends = 0
    prev_dead_ends = 0
    prev_report = 0
    timestep = 1.0
    def print_report(current_solution: PartialSolution, best_solution: PartialSolution):
        nonlocal dead_ends
        nonlocal prev_report
        nonlocal prev_dead_ends
        dead_ends += 1
        t = time.time()
        if (dt:=(t-prev_report)) < timestep and not best_solution.is_solved:
            return
        prev_report = t
        delta = (dead_ends-prev_dead_ends) / dt
        prev_dead_ends = dead_ends
        print()
        print(f"# Varianten probiert: {dead_ends:_d} ({delta:0.0f} / s)")
        print(f"Aktuell bei:               {current_solution}")
        print(f"Längste Teillösung bisher: {best_solution}")
    return print_report

def solve_cube(cube=cube_4, start_from="", report: ReportCB | None = None):
    """Solve cube.

    First step will go into +x and second into +y direction.
    """
    report = cast(ReportCB, report or print_report_factory())
    # Validate that the cube is valid
    N = cube.size
    if (s := sum(cube.segments)) != (t := (N * N * N)):
        raise ValueError("Invalid Snakecube: Sum of segments must be {t}, got {s}")
    start_from = start_from.replace(" ", "")
    ps = PartialSolution.init_for(cube)
    # First segment goes to +x. Always go to +y next.
    solution = solve_recurse(
        ps, ["+y"], ["0"], start_from=start_from, longest_overall=ps, report=report
    )
    return solution


class InteractiveSolver:
    def __init__(self, cube):
        self.prev_val = 0
        self.boxes = []
        self._slider = None
        pv.global_theme.color_cycler = "default"
        self.plotter = pv.Plotter()
        self.cube = cube
        self._solution = None
        self._new_solution = None
        self._finished = Event()
        self._print_report = print_report_factory()

    def solve_threadproc(self, start_from):
        self._finished.clear()
        self._print_report = print_report_factory()
        try:
            solution = solve_cube(
                self.cube,
                start_from=start_from,
                report=self.mm_report
            )
            self._new_solution = solution
        except Exception as e:
            import traceback

            traceback.print_exception(e)
        self._finished.set()

    def mm_report(
        self, current_solution: PartialSolution, best_solution: PartialSolution
    ):
        self._print_report(current_solution, best_solution)
        self._new_solution = current_solution

    def update_view(self, count: int):
        # If there is a new solution, view it
        if solution := self._new_solution:
            self._new_solution = None
            self._solution = solution
            plotter = self.plotter
            plotter.suppress_rendering = True
            first_draw = (not self.boxes)
            for box in self.boxes:
                plotter.remove_actor(box)
            plotter.set_color_cycler("default")
            self.boxes = self.plot_solution(solution)
            self.show_step(len(self.cube.segments), force=True)
            if self._finished.is_set():
                self._slider.EnabledOn()
            else:
                self._slider.EnabledOff()
            if first_draw:
                plotter.reset_camera()
            plotter.render()

    def show_step(self, val, force=False):
        val = int(val)
        if not force and self.prev_val == val:
            return
        prev_val = val
        for idx, box in enumerate(self.boxes):
            box.visibility = idx < val
            if idx < val:
                box.prop.opacity = 1 / (val - idx) ** 0.5

    def solve(self, start_from: str = "", movie_mode=False):
        solve_thread = Thread(target=self.solve_threadproc, args=(start_from,))
        solve_thread.daemon = True
        solve_thread.start()
        N = len(self.cube.segments)
        if not movie_mode:
            self._finished.wait()

        plotter = self.plotter
        plotter.add_timer_event(
            max_steps=1e10, duration=PLOT_INTERVAL_MS, callback=self.update_view
        )
        self._slider = plotter.add_slider_widget(
            self.show_step, (1, N), value=N, title="Step", interaction_event="always"
        )
        plotter.show(interactive=True)
        # interactive=True, interactive_update=True)

    def plot_solution(self, solution: PartialSolution) -> list[pv.Actor]:
        plotter = self.plotter
        cube = self.cube
        boxes = []
        x, y, z = -1, 0, 0
        shrink = 0.15
        for direction, length in zip(solution.segment_directions, cube.segments):
            # box bounds
            sign, axis = direction
            bounds = [
                x + shrink,
                x + 1 - shrink,
                y + shrink,
                y + 1 - shrink,
                z + shrink,
                z + 1 - shrink,
            ]
            if axis == "x":
                if sign == "-":
                    bounds[0] = x - length + shrink
                    bounds[1] = x + shrink
                    x += -length
                else:
                    bounds[0] = x + 1 - shrink
                    bounds[1] = x + 1 + length - shrink
                    x += length
            elif axis == "y":
                if sign == "-":
                    bounds[2] = y - length + shrink
                    bounds[3] = y + shrink
                    y += -length
                else:
                    bounds[2] = y + 1 - shrink
                    bounds[3] = y + 1 + length - shrink
                    y += length
            elif axis == "z":
                if sign == "-":
                    bounds[4] = z - length + shrink
                    bounds[5] = z + shrink
                    z += -length
                else:
                    bounds[4] = z + 1 - shrink
                    bounds[5] = z + 1 + length - shrink
                    z += length
            box = plotter.add_mesh(pv.Box(bounds=tuple(bounds)), show_edges=True)
            boxes.append(box)
        return boxes

def start_menu():
    print("Welchen Würfel lösen?")
    print()
    print("3 -> 3x3x3 WÜrfel {cube_3.segment_str}")
    print("4 -> 4x4x4 WÜrfel {cube_4.segment_str}")
    print("""
          .. oder eigenen Würfel eingeben:
            Würfel ist als Folge von Zahlen anzugeben.
            Jede Ziffer entspricht den Teilwürfelchen bis zum nächsten 90°-Knick.
            d.h. erster Abschnitt wird voll angegeben, die weiteren immer ohne den
            ersten Würfel (der ja schon gezählt wurde).
    """)
    s = input("> ")
    if s == "" or s == "3":
        cube = cube_3
    elif s == "4":
        cube = cube_4
    else:
        cube = Snakecube.from_segment_str(s)
    
    print()
    print("Ab einer vorherigen Faltung fortsetzen?")
    print("Beispieleingabe: 0 B L B F B L F F F B L ")
    print("Leer lassen, um bei Null anzufangen")
    start_from = input("> ").upper()

    print()
    print("Movie mode (J/N)? (verlangsamt die Lösung beträchtlich)")
    movie_mode = input("> ").upper() == "J"

    solver = InteractiveSolver(cube)
    solver.solve(start_from=start_from, movie_mode=movie_mode)


if __name__ == "__main__":
    start_menu()
    # Lösung 4er Würfel
    #start_from= "0 B L B F B L F L F B L B B R B L L R L F B F B B R R L B F F B L R L B L B R B B L B F B "
