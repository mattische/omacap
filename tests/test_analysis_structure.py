"""Finding the phrases a song repeats."""

from __future__ import annotations

from dataclasses import dataclass

from omacap.analysis.structure import find_phrases, form, label_phrases


@dataclass
class FakeBar:
    """Just enough of a bar for the structure code: a number and a label."""

    number: int
    label: str


def bars(labels: list[str]) -> list[FakeBar]:
    return [FakeBar(i + 1, label) for i, label in enumerate(labels)]


VERSE = ["C", "Em", "D", "D", "C", "Em", "D", "D"]
CHORUS = ["Em", "C", "Am", "C", "D", "D", "D", "Am"]
# Eight bars that do not repeat inside themselves, for the cases where that
# would be the thing found rather than the phrase under test.
PLAIN = ["C", "Em", "D", "D", "Am", "F", "G", "G"]


# -- finding --------------------------------------------------------------

def test_a_phrase_played_twice_in_a_row_is_found_once():
    phrases = find_phrases(bars(VERSE * 2))
    assert len(phrases) == 1
    assert phrases[0].repeats == 2
    assert phrases[0].length == 8


def test_the_bars_kept_are_the_first_time_through():
    phrase = find_phrases(bars(VERSE * 3))[0]
    assert [bar.label for bar in phrase.bars] == VERSE
    assert phrase.start == 1
    assert phrase.repeats == 3


def test_a_song_that_never_repeats_keeps_every_bar():
    labels = ["C", "F", "G", "Am", "Dm", "E", "A", "B"]
    phrases = find_phrases(bars(labels))
    assert all(not phrase.repeated for phrase in phrases)
    assert [bar.label for p in phrases for bar in p.bars] == labels


def test_every_bar_survives_the_round_trip():
    labels = ["G"] + PLAIN * 2 + ["D"] + CHORUS * 2 + ["A"]
    kept = [bar.label for p in find_phrases(bars(labels)) for bar in p.bars]
    # A repeat is written once, so the count drops, but the order never breaks.
    assert kept == ["G"] + PLAIN + ["D"] + CHORUS + ["A"]


def test_a_longer_phrase_wins_over_a_shorter_one_inside_it():
    # Four bars of C repeat as a 4-bar phrase, not as four 1-bar ones.
    phrase = find_phrases(bars(["C", "G", "Am", "F"] * 2))[0]
    assert phrase.length == 4
    assert phrase.repeats == 2


def test_repeats_must_be_next_to_each_other():
    # Verse, chorus, verse: the two verses are not one phrase played twice.
    phrases = find_phrases(bars(PLAIN + CHORUS + PLAIN))
    assert [p.repeats for p in phrases if p.repeated] == []


def test_a_phrase_that_repeats_inside_itself_collapses_to_its_loop():
    # VERSE is "C Em D D" twice over, so eight bars played twice is that
    # four-bar loop played four times, which is the shorter way to write it.
    phrase = find_phrases(bars(VERSE * 2))[0]
    assert phrase.length == 8 and phrase.repeats == 2


def test_no_bars_finds_no_phrases():
    assert find_phrases([]) == []


# -- labelling ------------------------------------------------------------

def test_repeated_phrases_are_lettered_in_order():
    phrases = label_phrases(find_phrases(bars(PLAIN * 2 + CHORUS * 2)))
    assert [p.letter for p in phrases] == ["A", "B"]


def test_a_phrase_played_once_gets_no_letter():
    phrases = label_phrases(find_phrases(bars(["C"] + PLAIN * 2)))
    assert phrases[0].letter == ""


def test_the_form_names_each_section_and_its_count():
    phrases = label_phrases(find_phrases(bars(PLAIN * 3 + CHORUS * 2)))
    assert form(phrases) == "A×3 B×2"


def test_a_song_with_no_repeats_has_no_form():
    assert form(find_phrases(bars(["C", "F", "G", "Am"]))) == ""
