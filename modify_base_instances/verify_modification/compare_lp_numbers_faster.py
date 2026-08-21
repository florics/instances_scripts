#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,sys
from collections import deque
from pathlib import Path
from typing import Deque,Iterator,Sequence

NUMBER_RE=re.compile(r'[+-]?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)')

def iter_numbers(path:Path)->Iterator[str]:
    with path.open('r',encoding='utf-8',errors='strict') as f:
        for line in f:
            for m in NUMBER_RE.finditer(line): yield m.group(0)

class Reader:
    def __init__(self,it:Iterator[str]): self.it=it; self.buf:Deque[str]=deque(); self.eof=False; self.consumed=0
    def fill(self,n:int):
        while len(self.buf)<n and not self.eof:
            try:self.buf.append(next(self.it))
            except StopIteration:self.eof=True
    def first(self):
        self.fill(1); return self.buf[0] if self.buf else None
    def peek(self,n:int)->list[str]:
        self.fill(n); out=[]
        for i,x in enumerate(self.buf):
            if i>=n: break
            out.append(x)
        return out
    def consume(self,n:int)->list[str]:
        self.fill(n); n=min(n,len(self.buf)); out=[self.buf.popleft() for _ in range(n)]; self.consumed+=n; return out

def shorten(xs:Sequence[str],limit:int)->str:
    if not xs:return '<empty>'
    if len(xs)<=limit:return ' '.join(xs)
    h=max(1,limit//2); t=max(1,limit-h)
    return ' '.join(xs[:h])+' ... '+' '.join(xs[-t:])

def find_resync(a:Sequence[str],b:Sequence[str],L:int):
    if len(a)<L or len(b)<L:return None
    p=min(4,L)
    idx={}
    for j in range(len(b)-L+1): idx.setdefault(tuple(b[j:j+p]),[]).append(j)
    best=None; bd=None
    for i in range(len(a)-L+1):
        js=idx.get(tuple(a[i:i+p]))
        if not js: continue
        for j in js:
            d=i+j
            if bd is not None and d>=bd: break
            if a[i:i+L]==b[j:j+L]: best=(i,j); bd=d; break
        if bd==i: break
    return best

def main():
    ap=argparse.ArgumentParser(description='Fast streaming exact-string comparison of numerical tokens in two LP files.')
    ap.add_argument('first_lp',type=Path); ap.add_argument('second_lp',type=Path)
    ap.add_argument('--anchor-length',type=int,default=20)
    ap.add_argument('--lookahead',type=int,default=20000)
    ap.add_argument('--show',type=int,default=30)
    ap.add_argument('--max-differences',type=int,default=1000)
    args=ap.parse_args()
    if args.anchor_length<=0 or args.lookahead<args.anchor_length or args.show<=0 or args.max_differences<0:
        print('ERROR: invalid arguments',file=sys.stderr); return 2
    for p in (args.first_lp,args.second_lp):
        if not p.is_file(): print(f'ERROR: not a file: {p}',file=sys.stderr); return 2
    a=Reader(iter_numbers(args.first_lp)); b=Reader(iter_numbers(args.second_lp))
    diffs=equal=da=db=0
    while True:
        x=a.first(); y=b.first()
        if x is None and y is None: break
        if x is None or y is None:
            diffs+=1
            print('='*80); print(f'Difference {diffs}: one file reached EOF first.')
            break
        if x==y:
            a.consume(1); b.consume(1); equal+=1; continue
        sa,sb=a.consumed,b.consumed
        n=args.lookahead+args.anchor_length
        aa=a.peek(n); bb=b.peek(n)
        r=find_resync(aa,bb,args.anchor_length)
        if r is None:
            print('='*80)
            print(f'No common anchor of length {args.anchor_length} within the next {args.lookahead:,} tokens.')
            print(f'first token index : {sa:,}\nsecond token index: {sb:,}')
            print('first preview : '+shorten(aa,args.show)); print('second preview: '+shorten(bb,args.show))
            print('Try increasing --lookahead.'); return 3
        ia,ib=r; xa=a.consume(ia); xb=b.consume(ib); diffs+=1; da+=len(xa); db+=len(xb)
        print('='*80); print(f'Difference {diffs}')
        print(f'first : [{sa:,}, {sa+len(xa):,}) ({len(xa):,} tokens)')
        print(f'second: [{sb:,}, {sb+len(xb):,}) ({len(xb):,} tokens)')
        print('first : '+shorten(xa,args.show)); print('second: '+shorten(xb,args.show))
        anch=a.peek(args.anchor_length)
        if anch==b.peek(args.anchor_length): print('resync: '+shorten(anch,args.show))
        if args.max_differences and diffs>=args.max_differences:
            print(f'Stopped after {diffs} differences.'); break
    print('='*80); print('Summary')
    print(f'consumed first : {a.consumed:,}\nconsumed second: {b.consumed:,}\nequal aligned  : {equal:,}\ndifferences    : {diffs:,}\ndiff tokens 1  : {da:,}\ndiff tokens 2  : {db:,}')
    return 0 if diffs==0 else 1

if __name__=='__main__': raise SystemExit(main())
