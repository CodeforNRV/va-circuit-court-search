import cookielib
import urllib
import urllib2
import os
import pickle
import pymongo
import pygal
import locale
import re
import sys
import json
import datetime
from bson.son import SON
from bs4 import BeautifulSoup
from flask import Flask, session, render_template, request
from pprint import pprint
from time import sleep
from bson.objectid import ObjectId
from werkzeug import Response

session = {}
user_agent = u"Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.6; en-US; " + \
    u"rv:1.9.2.11) Gecko/20101012 Firefox/3.6.11"

def handle_parse_exception(soup):
    print '\nException parsing HTML.', \
          'Probably contained something unexpected.', \
          'Check unexpected_output.html'
    with open('unexpected_output.html', 'wb') as output:
        output.write(soup.prettify().encode('UTF-8'))

def get_data_from_table(case, table):
    table_cells = table.find_all('td')
    for cell in table_cells:
        strings = list(cell.stripped_strings)
        if len(strings) < 2:
            continue
        name = strings[0].encode('ascii', 'ignore') \
                         .replace(':', '').replace(' ', '')
        value = strings[1].encode('ascii', 'ignore')
        case[name] = value

def get_names_from_table(case, table, party):
    case[party] = [list(x.stripped_strings) for x in table.find_all('li')]

def get_hearings_from_table(case, table):
    case['Hearings'] = []
    rows = table.find_all('tr')
    col_names = [x.encode('ascii') for x in rows.pop(0).stripped_strings]
    col_names[0] = 'Number'
    for row in rows:
        hearing = {}
        for i, col in enumerate(row.find_all('td')):
            val = col.get_text(strip=True) \
                     .encode('ascii', 'ignore')
            if val != '':
                hearing[col_names[i]] = val
        hearing_dt = hearing['Date'] + hearing['Time']
        hearing['Datetime'] = datetime.datetime.strptime(hearing_dt, "%m/%d/%y%I:%M%p")
        case['Hearings'].append(hearing)

def get_disposition_from_table(case, table):
    cells = [list(x.stripped_strings) for x in table.find_all('li')]
    for cell in cells:
        if len(cell) < 2:
            continue
        name = cell[0].encode('ascii', 'ignore') \
                      .replace(':', '').replace(' ', '')
        value = cell[1].encode('ascii', 'ignore')
        case[name] = value

def value_to_datetime(case, name):
    case[name] = datetime.datetime.strptime(case[name], "%m/%d/%y")

def get_case_details(opener, case):
    data = urllib.urlencode({
        'courtId':case['court'][:3],
        'caseNo':case['caseNumber'],
        'categorySelected':'CIVIL'
    })
    url = u"http://ewsocis1.courts.state.va.us/CJISWeb/CaseDetail.do"
    raw_html = opener.open(url, data).read()
    try:
        html = BeautifulSoup(raw_html)
        tables = html.find_all('table')
        details_table = tables[4]
        plaintiffs_table = tables[8]
        defendants_table = tables[10]
        hearings_table = tables[12]
        disposition_table = tables[14]
        get_data_from_table(case, details_table)
        get_names_from_table(case, plaintiffs_table, 'Plaintiffs')
        get_names_from_table(case, defendants_table, 'Defendants')
        get_hearings_from_table(case, hearings_table)
        get_disposition_from_table(case, disposition_table)
        if 'Filed' in case:
            value_to_datetime(case, 'Filed')
    except:
        handle_parse_exception(raw_html)
        raise

def getCases(html, name, names):
    for row in html.find(class_="nameList").find_all('tr'):
        cols = row.find_all('td')
        if len(cols) > 4:
            if name not in cols[1].string:
                return True
            names.append({
                'caseNumber': cols[0].span.a.string.strip(),
                'name': cols[1].string.strip(),
                'charge': cols[2].string.strip(),
                'date': cols[3].string.strip(),
                'status': cols[4].string.strip()
            })
        elif len(cols) > 3:
            if name not in cols[1].get_text() and name not in \
                    cols[2].get_text():
                return True
            first_party = cols[1].get_text() \
                                 .replace('\t', '') \
                                 .replace('\r', '') \
                                 .replace('\n', '') \
                                 .split(':')
            second_party = cols[2].get_text() \
                                  .replace('\t', '') \
                                  .replace('\r', '') \
                                  .replace('\n', '') \
                                  .split(':')
            names.append({
                'caseNumber': cols[0].span.a.string.strip(),
                'name': cols[1].get_text(),
                'otherName': cols[2].get_text(),
                first_party[0]: first_party[1].strip(),
                second_party[0]: second_party[1].strip(),
                'status': cols[3].string.strip()
            })
    return False

def lookupCases(opener, name, court, division):
    cases = []
    data = urllib.urlencode({
        'category': division,
        'lastName': name,
        'courtId': court,
        'submitValue': 'N'})
    cases_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/Search.do"
    searchResults = opener.open(cases_url, data)
    html = searchResults.read()
    if 'Search returned no records' in html:
        return cases
    done = True
    try:
        done = getCases(BeautifulSoup(html), name, cases)
    except:
        handle_parse_exception(html)
        raise
    data = urllib.urlencode({
        'courtId': court,
        'pagelink': 'Next',
        'lastCaseProcessed': '',
        'firstCaseProcessed': '',
        'lastNameProcessed': '',
        'firstNameProcessed': '',
        'category': division,
        'firstCaseSerialNumber': 0,
        'lastCaseSerialNumber': 0,
        'searchType': '',
        'emptyList': ''})

    search_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/Search.do"
    count = 1
    while(not done):
        sleep(1)
        print 'Search', str(count)
        count += 1
        search_results = opener.open(search_url, data)
        html = search_results.read()
        try:
            done = getCases(BeautifulSoup(html), name, cases)
        except:
            handle_parse_exception(html)
            raise
    return cases

#@app.route("/case/<caseNumber>/court/<path:court>")
def case_details(caseNumber, court):
    if 'cookies' not in session:
        return "Error. Please reload the page."
    courtId = court[:3]
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
    case_details = db['detailed_cases'].find_one({'court': court, 'caseNumber': caseNumber})
    if case_details is not None:
        print 'Found cached search'
        case_details['cached'] = True
    else:
        cookieJar = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar))
        opener.addheaders = [('User-Agent', user_agent)]

        for cookie in pickle.loads(session['cookies']):
            cookieJar.set_cookie(cookie)
        if 'courtId' not in session or session['courtId'] != courtId:
            print 'Changing court'
            data = urllib.urlencode({
                'courtId': courtId,
                'courtType': 'C',
                'caseType': 'ALL',
                'testdos': False,
                'sessionCreate': 'NEW',
                'whichsystem': court})
            place_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/MainMenu.do"
            opener.open(place_url, data)
        session['courtId'] = courtId
        case_details = {'court': court, 'caseNumber': caseNumber}
        get_case_details(opener, case_details)
        print 'Caching Search'
        db['detailed_cases'].insert(case_details)

#@app.route("/search/<name>/court/<path:court>")
def searchCourt(name, court):
    if 'cookies' not in session:
        return "Error. Please reload the page."

    courtId = court[:3]
    courtSearch = {'name': court[5:], 'id': courtId}

    db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
    cases = db['cases'].find_one({'name': name, 'court': court})
    if cases is not None:
        print 'Found cached search'
    else:
        cookieJar = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar))
        opener.addheaders = [('User-Agent', user_agent)]

        for cookie in pickle.loads(session['cookies']):
            cookieJar.set_cookie(cookie)
        if 'courtId' not in session or session['courtId'] != courtId:
            print 'Changing court'
            data = urllib.urlencode({
                'courtId': courtId,
                'courtType': 'C',
                'caseType': 'ALL',
                'testdos': False,
                'sessionCreate': 'NEW',
                'whichsystem': court})
            place_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/MainMenu.do"
            opener.open(place_url, data)
            session['courtId'] = courtId
        courtSearch['civilCases'] = lookupCases(opener, name.upper(),
                                                courtId, 'CIVIL')
        print 'Caching search', str(len(courtSearch['civilCases'])), 'cases found'
        db['cases'].insert({
            'name': name,
            'court': court,
            'civilCases': courtSearch['civilCases'],
            'dateSaved': datetime.datetime.utcnow()
        })

#@app.route("/search/<name>/courts")
def searchCourts(name):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
    result = {
        'name': name,
        'searches': {}
    }
    cases = db['cases'].find({'name': name})
    for case in cases:
        result['searches'][case['court']] = case
    return jsonify(**result)

#@app.route("/search/<name>")
def start():
    cookieJar = cookielib.CookieJar()
    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar))
    opener.addheaders = [('User-Agent', user_agent)]
    home = opener.open('http://ewsocis1.courts.state.va.us/CJISWeb/circuit.jsp')
    if 'cookies' in session: print 'Old cookie', session['cookies']
    session['cookies'] = pickle.dumps(list(cookieJar))
    print 'New cookie', session['cookies']
    session['courtId'] = None

    courts = []
    html = BeautifulSoup(home.read())
    for option in html.find_all('option'):
        courts.append({
            'fullName': option['value'],
            'id': option['value'][:3],
            'name': option['value'][5:]
        })
    return courts

def reduce_name(name):
    if 'COUNTY OF' in name: return None
    if 'CITY OF' in name: return None
    name = name.replace(';',' ')
    name_parts = name.split(' ')
    if len(name_parts) < 2 or not name_parts[0].endswith(','): return None
    name = ' '.join(name_parts[:2])
    return name

total_searches = 0

# get started
name = sys.argv[1].upper()
courts = start()
court_full_names = [c['fullName'] for c in courts]

# do first level search
db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
courts_searched = [c['court'] for c in db['cases'].find({'name': name.upper()})]
courts_to_search = set(court_full_names) - set(courts_searched)
for court in courts_to_search:
    print court
    sleep(1)
    searchCourt(name, court)
    total_searches += 1
    if total_searches > 100:
        total_searches = 0
        start()

# do second level search
# look at first level and find names to search second level
names_to_search = set()
searches = db['cases'].find({'name': name.upper()})
for search in searches:
    for case in search['civilCases']:
        plaintiff_name = case['Plaintiff']
        if plaintiff_name.startswith(name): continue
        plaintiff_name = reduce_name(plaintiff_name)
        if plaintiff_name is None: continue
        names_to_search.add(plaintiff_name)
# search the names
for court in court_full_names:
    # create a list of names we have already searched
    names_searched = set([c['name'] for c in db['cases'].find({'court': court}, {'name': True})])
    names_to_search_this_court = names_to_search - names_searched
    count_remaining = len(names_to_search_this_court)
    # search only names we need
    for name in names_to_search_this_court:
        print court, name, str(count_remaining), 'left'
        count_remaining -= 1
        sleep(1)
        searchCourt(name, court)
        total_searches += 1
        if total_searches > 100:
            total_searches = 0
            start()
    # create a list of case numbers we have details for
    cases_with_detail = set([c['caseNumber'] for c in db['detailed_cases'].find({'court': court}, {'caseNumber': True})])
    # and a list of case numbers we need details for
    cases_to_detail = set()
    for search in db['cases'].find({'court': court, 'name': {'$in': list(names_to_search)}}):
        for case in search['civilCases']:
            plaintiff_name = reduce_name(case['Plaintiff'])
            if plaintiff_name is None: continue
            if plaintiff_name not in names_to_search: continue
            cases_to_detail.add(case['caseNumber'])
    # get the ones we dont have
    cases_to_detail = cases_to_detail - cases_with_detail
    count_remaining = len(cases_to_detail)
    for case_number in cases_to_detail:
        print court, case_number, str(count_remaining), 'left'
        count_remaining -= 1
        sleep(1)
        case_details(case_number, court)
        total_searches += 1
        if total_searches > 100:
            total_searches = 0
            start()
