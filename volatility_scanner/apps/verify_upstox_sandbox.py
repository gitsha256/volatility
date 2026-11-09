"""Explicit sandbox-only place/modify/cancel checks. No live profile is accepted."""
import argparse
import json
from pathlib import Path

from volatility_scanner.brokers.upstox.api.client import APIClient, APIError, RequestError
from volatility_scanner.security.tokens import AuthError

ORDER = dict(quantity=1,product='D',validity='DAY',price=1,tag='integration-check',
             instrument_token='NSE_EQ|INE002A01018',order_type='LIMIT',transaction_type='BUY',
             disclosed_quantity=0,trigger_price=0,is_amo=False)


def order_ids(payload):
    data=payload.get('data',{})
    if isinstance(data,dict):
        if data.get('order_id'):return [data['order_id']]
        return data.get('order_ids',[])
    if isinstance(data,list):
        return [item['order_id'] for item in data if isinstance(item,dict) and item.get('order_id')]
    return []


def smoke(client):
    if client.profile != 'sandbox':
        raise RequestError('Mutation verification is sandbox-only')
    results=[]
    def invoke(name,**kwargs):
        try:
            payload=client.call(name,**kwargs)
            results.append(dict(operation=name,status=payload.get('status','received'),http=200))
            return payload
        except (APIError,RequestError) as error:
            results.append(dict(operation=name,status='failed',error=str(error)))
            return {}
    for place,modify,cancel in (('placeOrder','modifyOrder','cancelOrder'),
                                 ('placeOrder_1','modifyOrder_1','cancelOrder_1')):
        ids=order_ids(invoke(place,body=ORDER))
        if ids:
            invoke(modify,body=dict(order_id=ids[0],quantity=1,price=1.1,order_type='LIMIT',
                                   validity='DAY',disclosed_quantity=0,trigger_price=0))
            invoke(cancel,query={'order_id':ids[0]})
        else:
            results.extend(dict(operation=name,status='skipped',reason='No sandbox order ID returned')
                           for name in (modify,cancel))
    payload=invoke('placeMultiOrder',body=[dict(ORDER,slice=False,correlation_id='check-1'),
                                         dict(ORDER,slice=False,correlation_id='check-2')])
    for identifier in order_ids(payload):
        invoke('cancelOrder',query={'order_id':identifier})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('D:/market-data/volatility/verification/sandbox.json'))
    args=parser.parse_args()
    with APIClient('sandbox') as api:
        result=smoke(api)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if any(row['status'] not in ('success',) for row in result):
        raise SystemExit(1)


if __name__=='__main__':
    try:main()
    except (AuthError,APIError,RequestError) as error:
        print(error)
        raise SystemExit(1)
