"""Offline dispatch/validation benchmark. No HTTP requests are sent."""
import argparse
import json
import time
from volatility_scanner.brokers.upstox.api.client import APIClient
from volatility_scanner.brokers.upstox.api.credentials import Credentials
from volatility_scanner.apps.verify_upstox_sandbox import ORDER


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations',type=int,default=10000)
    args=parser.parse_args()
    if args.iterations < 1:parser.error('Iterations must be positive')
    start=time.perf_counter_ns()
    with APIClient(credential=Credentials('sandbox','benchmark-only')) as sandbox, APIClient(
        credential=Credentials('algo','benchmark-only')) as live:
        cold=(time.perf_counter_ns()-start)/1e6
        output={'catalogue_operations':len(sandbox.catalogue.operations),'initialization_ms':round(cold,3)}
        for label,api,operation,kwargs in [('read',live,'getProfile',{}),('order',sandbox,'placeOrder',{'body':ORDER})]:
            start=time.perf_counter_ns()
            for _ in range(args.iterations):api.prepare(operation,**kwargs)
            elapsed=time.perf_counter_ns()-start
            output[label+'_prepare_us']=round(elapsed/args.iterations/1000,3)
        output['note']='Offline validation/routing cost; excludes network and broker latency'
        print(json.dumps(output,indent=2))


if __name__=='__main__':main()
