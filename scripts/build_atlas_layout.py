"""Сравнить UMAP/t-SNE локально и отдельно опубликовать выбранную проекцию."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    build=commands.add_parser('compare',help='только локальные данные и веса; без Telegram')
    build.add_argument('--input',type=Path,default=ROOT/'web/data/channels.json')
    build.add_argument('--post-cache',type=Path,default=ROOT/'reports/classification-v11/catalog-run/posts')
    build.add_argument('--output',type=Path,required=True)
    build.add_argument('--limit',type=int)
    build.add_argument('--frozen-evidence',type=Path,help='evidence.json прежнего сравнения; использовать вместе с его snapshot.json')
    build.add_argument('--methods',nargs='+',choices=['umap','tsne'],default=['umap','tsne'])
    apply=commands.add_parser('publish',help='проверить/явно применить выбранный JSON, не меняя каналы')
    apply.add_argument('--input',type=Path,required=True)
    apply.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    os.environ.setdefault('HF_HUB_OFFLINE','1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM','false')
    os.environ.setdefault('NUMBA_NUM_THREADS','4')
    os.environ.setdefault('OMP_NUM_THREADS','4')
    os.environ.setdefault('MKL_NUM_THREADS','4')
    try:
        from src.atlas_projection import compare,publish
        if args.command=='compare':
            report=compare(args.input,args.post_cache,args.output,limit=args.limit,methods=args.methods,frozen_evidence=args.frozen_evidence)
            print(args.output/'comparison.html')
            return 0 if all(x['status']=='ok' for x in report['methods'].values()) else 1
        count=publish(args.input,apply=args.apply)
        print(json.dumps(dict(channels=count,applied=args.apply)))
        return 0
    except (ValueError,OSError,ImportError,KeyError) as exc:
        print(str(exc),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
