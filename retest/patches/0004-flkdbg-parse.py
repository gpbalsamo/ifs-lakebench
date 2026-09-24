import sys, collections
def load(v):
    E, N, X = {}, {}, {}
    for line in open(f'/etc/ecmwf/nfs/dh2_perm_a/pad/ifs-lakebench/retest/dbg_{v}/out/Ar-001_2017-2022/run.log'):
        if not line.startswith(' FLKDBG') and not line.startswith('FLKDBG'): continue
        p = line.split(); tag, step = p[0], int(p[1]); vals = [float(x) for x in p[2:]]
        {'FLKDBG_E': E, 'FLKDBG_N': N, 'FLKDBG_X': X}[tag][step] = vals
    return E, N, X
