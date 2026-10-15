from pathlib import Path
import random
out=[]
def add(name,s,limit,tail=b'',bp=b''):
    out.append('\t'.join((name,s.hex(),str(limit),tail.hex(),bp.hex())))
prefixes=[('ground',b''),('csi-entry',b'\x1b['),('csi-intermediate',b'\x1b[ '),('csi-param',b'\x1b[1'),('dcs-entry',b'\x1bP'),('dcs-intermediate',b'\x1bP '),('dcs-param',b'\x1bP1'),('dcs-string',b'\x1bPq'),('escape',b'\x1b'),('escape-intermediate',b'\x1b '),('osc',b'\x1b]'),('sos',b'\x1bX'),('pm',b'\x1b^'),('apc',b'\x1b_')]
for state,prefix in prefixes:
    for byte in range(0x80,0xa0):
        for limit in [0,1,3,8]:
            add(f'c1.{state}.{byte:02x}.{limit}',b'a'+prefix+bytes([byte])+'试 é-y/z\x1b[0m'.encode(),limit,b'~' if limit==3 else b'',b'/')
for byte in range(256):
    for limit in [1,4]:
        add(f'byte.{byte:02x}.{limit}',b'x'+bytes([byte])+b' y',limit,b'~')
seeds=[b'\xb5\x8b'+ '试'.encode(),b'x\x1b]0;t'+ '✅y z'.encode(),b'\xc0\xaf',b'\xc2',b'\xc2x',b'\xe0\x80\x80',b'\xed\xa0\x80',b'\xe2\x82',b'\xf0\x80\x80\x80',b'\xf4\x90\x80\x80',b'\xf0\x9f\x92',b'\xff'+ '\u0301'.encode(),b'\xe2'+ '\u0301'.encode(),b'\xe2\x9c\x85',b'\x1bPq\xff\x9cABC', b'\xff\xfe\xfd'+ '测试'.encode()]
for i,s in enumerate(seeds):
    for limit in [0,1,2,3,5,9]:
        add(f'malformed.{i}.{limit}',s,limit,'…'.encode() if limit==5 else b'',b'\xff/' if limit==2 else b'')
# ui.Truncate capacity/NUL/panic outcomes remain part of the shared path.
for n in [2,4,12,20,33,40,60,80]:
    for count in [20,33,35]:
        add(f'capacity.{count}.{n}',('测'*count).encode(),n)
r=random.Random(7142026)
parts=[bytes([b]) for b in range(256)]+[p for _,p in prefixes]+[s.encode() for s in ['试','✅','é','👨\u200d👩\u200d👧','🇺🇸','\u0600','\u200b','\u00a0','\u2003','\r\n']]
for i in range(256):
    s=b''.join(r.choice(parts) for _ in range(r.randrange(1,18)))
    add(f'mixed.{i}',s,r.randrange(0,14),r.choice([b'',b'~',b'\x8b','…'.encode()]),r.choice([b'',b'/-',b'\xff','试'.encode()]))
for i in range(128):
    s=''.join(r.choice(['ASCII','试','✅','é','👨\u200d👩\u200d👧','🇺🇸','\u0600','\u200b','\u00a0','\u2003','\r\n','\t','-','/',' ','\x1b[31m','\x1b[0m']) for _ in range(r.randrange(1,12))).encode()
    add(f'valid.{i}',s,r.randrange(0,14),r.choice([b'',b'~','…'.encode()]),b'/')
Path('/home/ajs/.cache/pb-phase7/style-byte-probe/cases.tsv').write_text('\n'.join(out)+'\n')
print(len(out))
