"""Finding the phrases a song repeats, so a chart can say so once.

A chart of a four minute song is well over a hundred bars, and most of them are
the same eight bars written out again. Musicians do not write it that way; they
write the phrase once and say how many times. Collapsing the repeats is the
difference between a chart you read and a chart you scroll.

Nothing here is a claim about form. A repeated phrase is labelled with a letter
so the eye can match it to its other appearances, not "verse" or "chorus", which
would be guessing at something the audio does not carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Phrase lengths looked for, longest first. Eight and four are what popular music
#: is built from. Two was tried and is not worth a block of its own: on material
#: with little structure it made charts longer rather than shorter.
PHRASE_LENGTHS = (8, 4)

#: Letters given to repeated phrases, in the order they first appear.
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class Phrase:
    """A run of bars, and how many times it repeats."""

    bars: list = field(default_factory=list)   # the bars of one repetition
    repeats: int = 1
    letter: str = ""

    @property
    def start(self) -> int:
        """The bar number this phrase starts at."""
        return self.bars[0].number if self.bars else 0

    @property
    def length(self) -> int:
        return len(self.bars)

    @property
    def total_bars(self) -> int:
        return self.length * self.repeats

    @property
    def repeated(self) -> bool:
        return self.repeats > 1


def find_phrases(bars, lengths=PHRASE_LENGTHS) -> list[Phrase]:
    """Split the bars into phrases, collapsing anything written twice in a row.

    Greedy and longest-first: an eight bar phrase repeated is worth more to a
    reader than the four bar phrase inside it repeated twice as often.
    """
    labels = [bar.label for bar in bars]
    total = len(bars)
    phrases: list[Phrase] = []
    index = 0
    while index < total:
        for length in sorted(lengths, reverse=True):
            if index + 2 * length > total:
                continue
            group = labels[index: index + length]
            if group != labels[index + length: index + 2 * length]:
                continue
            repeats = 2
            while (
                index + (repeats + 1) * length <= total
                and group == labels[index + repeats * length: index + (repeats + 1) * length]
            ):
                repeats += 1
            phrases.append(Phrase(bars=list(bars[index: index + length]), repeats=repeats))
            index += repeats * length
            break
        else:
            phrases.append(Phrase(bars=[bars[index]], repeats=1))
            index += 1
    return label_phrases(phrases)


def label_phrases(phrases: list[Phrase]) -> list[Phrase]:
    """Give each distinct repeated phrase a letter, reusing it when it comes back."""
    seen: dict[tuple, str] = {}
    for phrase in phrases:
        if not phrase.repeated:
            continue
        key = tuple(bar.label for bar in phrase.bars)
        if key not in seen and len(seen) < len(LETTERS):
            seen[key] = LETTERS[len(seen)]
        phrase.letter = seen.get(key, "")
    return phrases


def form(phrases: list[Phrase]) -> str:
    """The shape of the song, as the letters in order. Empty when there is none."""
    parts = []
    for phrase in phrases:
        if not phrase.letter:
            continue
        parts.append(
            f"{phrase.letter}×{phrase.repeats}" if phrase.repeats > 1 else phrase.letter
        )
    return " ".join(parts)
