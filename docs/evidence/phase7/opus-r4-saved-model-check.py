#!/usr/bin/env python3
"""Compare a Python interpretation of proposed MatchProgress with actual Go.

This is source/design verification, not Rust execution or successor acceptance.
Includes stale byte positions, exact int64 scores, faults and stable rankings.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

REPO=Path(__file__).resolve().parents[3]
CACHE=Path('/home/ajs/.cache/pb-phase7/opus-r4-saved')


def wrap(value):
    return (value+(1<<63))%(1<<64)-(1<<63)


def load_unicode():
    source=(REPO/'src/tui/saved_fuzzy_unicode.rs').read_text()
    tables={}
    for name in ('FOLD','LOWER','UPPER'):
        body=source.split('const '+name+':',1)[1].split('&[',2)[2].split('];',1)[0]
        tables[name]=[tuple(int(value.strip(),0) for value in row.split(','))
                      for row in re.findall(r'\(([^()]+)\)',body)]
    return tables


TABLES=load_unicode()
FOLDS=dict(TABLES['FOLD'])


def equal(a,b):
    def fold(c):
        value=ord(c)
        return value-32 if 97<=value<=122 else FOLDS.get(value,value)
    return a==b or fold(a)==fold(b)


def category(c,name):
    value=ord(c)
    return any(lo<=value<=hi and (value-lo)%stride==0 for lo,hi,stride in TABLES[name])


@dataclass
class Progress:
    consumed:int=0
    retained:int|None=None
    best:int=-1
    last_applied:int|None=None
    adjacent:int=0
    total:int=0
    indexes:list=field(default_factory=list)  # characterization only

    def consider(self,byte,rune,previous):
        score=10 if byte==0 else 0
        if previous is not None:
            prior_byte,prior=previous
            score+=20 if category(prior,'LOWER') and category(rune,'UPPER') else 0
            score+=20 if prior in '/-_ .\\' else 0
            if self.last_applied==prior_byte:
                bonus=wrap(self.adjacent*2+5)
                self.adjacent=wrap(self.adjacent+bonus)
                score=wrap(score+bonus)
        if score>self.best:
            self.best=score
            self.retained=byte

    def apply(self):
        if self.retained is not None:
            if self.consumed==0:
                self.total=wrap(self.total+max(wrap(-5*self.retained),-15))
            self.total=wrap(self.total+self.best)
            self.last_applied=self.retained
            self.indexes.append(self.retained)
            self.consumed+=1
            self.best=-1


def score(pattern,title):
    if not pattern:
        return None,None,[]
    progress=Progress(); previous=None; byte=0
    for position,rune in enumerate(title):
        if progress.consumed==len(pattern):
            return None,f'runtime error: index out of range [{progress.consumed}] with length {len(pattern)}',progress.indexes
        if equal(rune,pattern[progress.consumed]):
            progress.consider(byte,rune,previous)
        next_rune=title[position+1] if position+1<len(title) else '\0'
        next_pattern=pattern[progress.consumed+1] if progress.consumed+1<len(pattern) else '\0'
        if next_rune=='\0' or equal(next_pattern,next_rune):
            progress.apply()
        previous=(byte,rune);byte+=len(rune.encode())
    total=wrap(progress.total+progress.consumed-len(title.encode()))
    return total if progress.consumed==len(pattern) else None,None,progress.indexes


def main():
    oracle_path=Path(__file__).with_name('opus-r4-saved-go-oracle.json')
    oracle=json.loads(oracle_path.read_text())
    records=[]
    for actual in oracle['results']:
        matches=[]; fault=None
        for index,title in enumerate(actual['Titles']):
            value,fault,indexes=score(actual['Pattern'],title)
            if fault is not None:
                break
            if value is not None:
                matches.append(dict(Str=title,Index=index,Score=value,MatchedIndexes=indexes))
        assert fault==actual.get('Panic'), (actual['Name'],fault,actual.get('Panic'))
        if fault is None:
            assert matches==(actual['Matches'] or []), (actual['Name'],matches,actual['Matches'])
            ranks=[dict(Index=match['Index'],MatchedIndexes=match['MatchedIndexes'])
                   for match in sorted(matches,key=lambda match:-match['Score'])]
            assert ranks==(actual['Ranks'] or []), (actual['Name'],ranks,actual['Ranks'])
        records.append(dict(name=actual['Name'],passed=True))
    sha=lambda data:hashlib.sha256(data).hexdigest()
    record=dict(actual_go_controls=len(records),source_interpretation_passed=True,
                matched_byte_positions_checked=True,exact_scores_rankings_and_faults_checked=True,
                rust_executed=False,proposed_sha256=sha((CACHE/'src-tui-saved.proposed.rs').read_bytes()),
                oracle_sha256=sha(oracle_path.read_bytes()),runner_sha256=sha(Path(__file__).read_bytes()),
                results=records,scope='Python interpretation only; native compilation/tests and successor process replay pending')
    Path(__file__).with_name('opus-r4-saved-model-check.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({key:value for key,value in record.items() if key!='results'},indent=2))


if __name__=='__main__':
    main()
