import csv
import pymongo
import os
import sys
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime

term = sys.argv[1]
db = pymongo.Connection(os.environ['MONGO_URI'])['court-search-temp']
cases = {}
earliest_filed = datetime.now()
case_types = []
docs = db['civil_cases'].find({'search_term': {'$regex':'^' + term}})
for row in docs:
    if row['Plaintiff'].startswith('CARILION') or \
       row['Plaintiff'].startswith('CITY OF') or \
       row['Plaintiff'].startswith('COUNTY OF'):
        continue
    if 'FilingType' in row:
        case_types.append(row['FilingType'])
    if 'Filed' in row and type(row['Filed']) is datetime and row['Filed'] < earliest_filed:
        earliest_filed = row['Filed']
    for key, value in cases.iteritems():
        a = key
        b = row['Plaintiff']
        if len(a) < len(b):
            b = b[:len(a)]
        else:
            a = a[:len(b)]
        if SequenceMatcher(None, a, b).ratio() > 0.9:
            value.append(row)
            break
    cases[row['Plaintiff']] = []
    cases[row['Plaintiff']].append(row)
names = []
with open(term + '.csv', 'w') as f:
    f.write('Earliest Filed:' + earliest_filed.strftime('%m/%d/%y') + '\n')
    for case_type in sorted(Counter(case_types).items(), key=lambda x: x[1], reverse=True):
        f.write(case_type[0] + ' ' + str(case_type[1]) + '\n')
    f.write('\n')
    for key, value in cases.iteritems():
        if len(value) > 1:
            match = False
            court_name = value[0]['court']
            for x in value:
                if x['court'] != court_name:
                    match = True
                    break
            if match:
                names.append(key)
                f.write(key + '\n')
                cases = sorted(value, key=lambda x: x['Filed'] if 'Filed' in x else datetime.now())
                for x in cases:
                    f.write(x['court'] + ',')
                    f.write(x['Plaintiff'] + ',')
                    f.write(x['Defendant'] + ',')
                    f.write(x['caseNumber'] + '\n')
                    if 'Filed' in x:
                        f.write('\t' + x['Filed'].strftime('%m/%d/%y') + ',')
                        f.write(x['FilingType'] + '\n')
                        for hearing in x['Hearings']:
                            f.write('\t' + hearing['Datetime'].strftime('%m/%d/%y') + ',')
                            f.write(hearing['Type'] + ',')
                            if 'Result' in hearing:
                                f.write(hearing['Result'])
                            f.write('\n')
                        if 'ConcludedBy' in x:
                            f.write('\t' + x['FinalOrderDate'] + ',')
                            f.write(x['Judgment'] + ',')
                            f.write(x['ConcludedBy'] + '\n')
                    f.write('\t' + x['status'] + '\n')
                f.write('\n')
