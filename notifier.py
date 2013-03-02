"""
url_notify.py - Phenny module to check websites for updates
Rewritten again by Mozai
This is free and unencumbered software released into the public domain.
"""
import httplib, time, re, rfc822, threading, urllib

# -- config
# global time between checks & min time between alerts
# any 'delay' setting in SITES less than this will act as
# this number anyways.
LOOPDELAY = 30

# after detecting the URL's Last-Modified time has changed, how many
# checks should be skipped before we start checking again?
# this will be ( SITES[site]['delay'] * LOOPS_SKIPPED ) seconds.
LOOPS_SKIPPED = 10

# timeout between command responses
# bots shouldn't spam, even when asked to
TELLDELAY = 60

# sites to check
# 'cmd' : {   used for '.cmd' to report last mtime
#  'name': used when reporting last mtime and in default mesg
#  'url': 'http://host/path/blah',
#  'url': ('starting url', regexp to next url, regexpt to next url, ...)
#  'delay': minimum seconds between checks,
#  'dest': (channel to announce, nick to notify) ,
#          defaults to all channels bot was configured to join
#  'mesg': what do say. defaults to "${name} updated Xm Ys ago"
#  }

SITES = {
  'update' : {
    'name': 'MSPA',
    'url' : 'http://mspaintadventures.com/rss/rss.xml',
    'mesg': '======= ======= UPDATE UPDATE ======= =======\n' +
            '======= http://mspaintadventures.com/ =======\n' +
            '======= ======= UPDATE UPDATE ======= =======\n'
  },
  'prequel' : {
    'name': 'PREQUEL',
    'url' : 'http://www.prequeladventure.com/feed/',
    'delay': 300,
    'mesg': 'Prequel update: http://www.prequeladventure.com/'
  },
#  'sbahj' : {
#    'name': 'SBaHJ',
#    'url' : ('http://www.mspaintadventures.com/sweetbroandhellajeff/',
#            r'="(.*?)"><img src="new.jpg"',
#            r'<img src="(archive/.*?)"',
#            ),
#    'delay': 60,
#    'dest': ('#farts','Mozai'),
#    'mesg': '= JESUS DICK ==================hornse==\n' +
#            '=== THERSE A SWEET HELLA UPDTE ======-=\n'
#  },
# TODO: tumblr doesn't do Last-Modified headers, so gotta use RSS
# instead: get all the <pubdate>, sort them, pluck the most recent,
# converted it to GMT, and use THAT as the mtime.
#  'mspandrew' : {
#    'name': 'Hussie\'s Tumblr',
#    'url' : 'http://mspandrew.tumblr.com/rss',
#  },
}

# --- end config

for SITE in SITES:
    # set defaults
    SITES[SITE]['mtime'] = 0
    SITES[SITE]['atime'] = 0
    SITES[SITE]['alert'] = False
    SITES[SITE].setdefault('name', SITE)
    SITES[SITE].setdefault('delay', LOOPDELAY)
    SITES[SITE]['delay_boost'] = 0
    SITES[SITE].setdefault('mesg', "\x02%s has updated\x02" % SITES[SITE]['name'])

def notify_owner(phenny, mesg):
    " carp to bot's owner, not to the channel "
    if hasattr(phenny,'bot'):
        phenny = phenny.bot
    try:
        ownernick = phenny.config.owner
        phenny.msg(ownernick, mesg)
    except AttributeError:
        # no owner configured? we carp to console anyways
        pass
    phenny.log('*** '+mesg)

def _update_siterecord(site):
    " update the SITES dict() if desired. may throw socket.timeout "
    # I packaged this into a seperate function to please pylint & PEP8
    # This isn't thread-safe; would be better to get a lock on SITES[site]
    # while I'm doing the update, but there *shouldn't* be multiple
    # threads checking URLs for update.  ... *shouldn't*
    url = site['url']
    now = time.mktime(time.gmtime(time.time())) # now BEFORE check
    if (site['atime'] + site['delay'] + site['delay_boost'] > now):
        # too soon to check again
        return None
    if isinstance(url, (list, tuple)):
        # it's a chain
        url = site['url'][0]
        for step in site['url'][1:]:
            regexp = re.compile(step)
            (host, path) = urllib.splithost(urllib.splittype(url)[1])
            # using httplib so I can have 'timeout' that isn't in urllib.
            try:
                conn = httplib.HTTPConnection(host, timeout=3)
                conn.request('GET', path)
                response = conn.getresponse()
            except Exception as err:
                site['delay_boost'] = 300
                raise Exception('site %s get %s failed: %s' % (site['name'], url, repr(err)))
            if (response.status >= 300 and response.status < 400):
                site['url'] = None # skip checking from now on
                raise Exception('site %s should use url %s' % (site['name'], response.getheader('Location')))
            match = regexp.search(response.read())
            conn.close()
            if match:
                url = urllib.basejoin(url, match.group(1))
            else:
                url = None
                break
    if url :
        site['delay_boost'] = 0 # resume normal schedule
        (host, path) = urllib.splithost(urllib.splittype(url)[1])
        try:
            connect = httplib.HTTPConnection(host, timeout=2)
            connect.request("HEAD", path)
            response = connect.getresponse()
        except Exception as err:
            site['delay_boost'] = 300
            raise Exception('site %s head %s failed: %s' % (site['name'], url, repr(err)))
        if (response.status >= 300 and response.status < 400):
            site['url'] = None # skip checking from now on
            raise Exception('site %s should use url %s (%s)' % (site['name'], response.getheader('Location'), url))
        mtime = response.getheader('last-modified', None)
        if mtime == None :
            site['url'] = None # skip checking from now on
            raise Exception('site %s doesnt use Last-Modified headers (%s)' % (site['name'], url))
        new_mtime = time.mktime(rfc822.parsedate(mtime))
        if site['mtime'] == 0:
            # we haven't checked yet
            site['mtime'] = new_mtime
        if new_mtime != site['mtime'] :
            # this also catches it if the URL's Last-Modified jumped backwards
            site['alert'] = True
            if site['delay'] < 60*60 :
                # if site delay is less than an hour, skip the next few checks
                site['delay_boost'] = site['delay'] * LOOPS_SKIPPED
            site['mtime'] = new_mtime
        site['atime'] = time.mktime(time.gmtime(time.time())) # now AFTER check
        return url

def url_notify_loop(phenny):
    " update SITES cache; if they changed recently carp about it "
    my_thread_name = threading.current_thread().name
    while phenny.notify_threadname == my_thread_name :
        for sitekey in SITES:
            if not phenny.notify_threadname == my_thread_name :
                break
            try:
                site = SITES[sitekey]
                _update_siterecord(site)
                if site['alert'] :
                    if 'dest' in site:
                        targets = site['dest']
                    else:
                        targets = phenny.channels
                    for dest in targets:
                        for line in site['mesg'].split("\n") :
                            phenny.msg(dest, line)
                    site['alert'] = False # alert no longer pending
            except:
                # this next ugly piece of crap is so I can
                # properly do an exception the Phenny-way without
                # annoying everyone on every channel
                class OriginFake(): pass
                origin = OriginFake()
                origin.sender = phenny.config.owner
                phenny.error(origin)
        if phenny.notify_threadname == my_thread_name :
            time.sleep(LOOPDELAY)

def setup(phenny):
    " starts url_notify_loop() "
    # PhennyWrapper was such a bad idea; never saw the point.
    if hasattr(phenny, 'bot') :
        phenny = phenny.bot
    thr_args = (phenny,)
    thr = threading.Thread(target=url_notify_loop, args=thr_args)
    # The next line is ugly, but no other choice it seems.
    phenny.notify_threadname = thr.name
    thr.start()

def url_notify_stop(phenny, cmd_in):
    " clobbers a flag to make url_notify_loop commit sudoku "
    who = cmd_in.nick
    if cmd_in.admin :
        phenny.msg(who,'stopping notify (%s)' % (phenny.bot.notify_threadname))
        phenny.bot.notify_threadname = None

url_notify_stop.commands = ['notifystop','stopnotify']

def url_notify_start(phenny, cmd_in):
    " re-runs setup() unless there's already a thread running "
    who = cmd_in.nick
    if not cmd_in.admin :
        return
    if phenny.bot.notify_threadname :
        phenny.msg(who,'notify thread already running (%s)' % phenny.bot.notify_threadname)
    else:
        phenny.msg(who,'restarting notify loop')
        setup(phenny.bot)

url_notify_start.commands = ['notifystart','startnotify']

# ---

def _secsToPretty(ticks=0):
    " given ticks as a duration in seconds, in human-friendly units "
    day, remain    = divmod(ticks, (24*60*60))
    hour, remain   = divmod(remain, (60*60))
    minute, second = divmod(remain, 60)
    if (day > 0):
        return "%dd %dh" % (day, hour)
    elif (hour > 0):
        return "%dh %dm" % (hour, minute)
    elif (minute > 0):
        return "%dm %ds" % (minute, second)
    else:
        return "less than a minute"

def tell_last_update(phenny, cmd_in):
    " utters the last mtime of a known site "
    now = time.mktime(time.gmtime(time.time()))
    self = phenny.bot.variables['tell_last_update']
    who = cmd_in.nick
    site = SITES[cmd_in.group(1)]
    if site['atime'] <= 0 :
        mesg = "%s hasn't been checked yet" % (site['name'])
    elif site['mtime'] <= 0 :
        mesg = "%s latest update couldn't be fetched" % (site['name'])
    else:
        when_ago = _secsToPretty(now - site['mtime'])
        mesg = "%s updated %s ago" % (site['name'], when_ago)
    if (self.atime + TELLDELAY) <= now :
        phenny.say(mesg)
    else :
        phenny.msg(who, mesg)

tell_last_update.commands = SITES.keys()
tell_last_update.thread = False  # don't bother, non-blocking
tell_last_update.atime = 0

## --
#
#def dump_SITES(phenny, cmd_in):
#  " debug only "
#  del(phenny) # shut up pylint
#  if not cmd_in.admin :
#    return
#  print("---")
#  print(repr(SITES))
#
#dump_SITES.commands = ['dumpsites',]
#
