# -*- coding: utf-8 -*-
###
# Copyright (c) 2013, spline
# All rights reserved.
#
#
###

# my libs.
import re  # regex for matching urls.
import json  # json for APIs.
import requests  # http stuff.
import time  # longurl cache.
import sqlite3 as sqlite  # linkdb.
import os  # linkdb
import magic  # python-magic
from bs4 import BeautifulSoup  # bs4
from urlparse import urlparse, parse_qs, urlsplit, urlunsplit, urlunparse
from cStringIO import StringIO  # images.
# extra supybot libs
import supybot.ircmsgs as ircmsgs
import supybot.conf as conf

from datetime import datetime, timedelta
from dateutil.tz import tzutc
from dateutil.parser import parse as parse_datetime
import time

# supybot libs
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
from supybot.i18n import PluginInternationalization, internationalizeDocstring

_ = PluginInternationalization('Titler')

LINKDB = conf.supybot.directories.data.dirize("Titler.db")

class LinkDB:
    def __init__(self):
        self.testinit()

    def testinit(self):
        """Test if DB exists. Create if not."""

        if not os.path.exists(LINKDB):
            try:
                F = open(LINKDB, "w")
                F.close()
            except IOError, ioe:
                callbacks.Plugin.log.debug("IOError in DB creation")
        else:
            return
        with sqlite.connect(LINKDB) as conn:
            query = """
            CREATE TABLE IF NOT EXISTS `links` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `time` DATETIME NOT NULL,
                `url` TEXT NOT NULL,
                `title` TEXT,
                `channel` TEXT NOT NULL,
                `user` TEXT NOT NULL
            );
            """
            cur = conn.cursor()
            cur.executescript(query)
            cur.commit()

    def __safe_unicode(self, s):
        """Return the unicode representation of obj"""

        try:
            return s.decode("utf-8").encode("utf-8")
        except UnicodeDecodeError:
            return s.decode("iso8859-1").encode("utf-8")

    def add(self, url, title, channel, user):
        """Insert a link into the DB."""

        with sqlite.connect(LINKDB) as conn:
            query = "INSERT INTO links VALUES (NULL, strftime('%s', 'now'), ?, ?, ?, ?)"
            conn.text_factory = str
            results = conn.execute(query, (url, title, channel, user))
        return True

    def check(self, link):
        """Determine whether the link should be fetched again. """

        with sqlite.connect(LINKDB) as conn:
            query = "SELECT title, channel, user FROM links WHERE link LIKE ? AND (strftime('%s', 'now') - last_access) >= ?;"
            results = conn.execute(q, (link, 86400 * 7))
            results = results.fetchone()
        return True if results is not None else False

    def update(self, link):
        """docstring for updateLinkLastseen"""
        with sqlite.connect(LINKDB) as conn:
            q = u"UPDATE links SET last_access=strftime('%s', 'now') WHERE link LIKE ?;"
            results = conn.execute(q, [link])
        return True if results is not None else False


class Titler(callbacks.Plugin):
    """Titler plugin."""
    threaded = True
    noIgnore = False

    def __init__(self, irc):
        self.__parent = super(Titler, self)
        self.__parent.__init__(irc)
        self.encoding = 'utf8'  # irc output.
        self.headers = {'User-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.84 Safari/537.36'}
        # longurl stuff
        # self.longUrlCacheTime = time.time()
        # self.longUrlServices = None
        # self._getlongurlservices()  # initial fetch.
        # bitly.
        self.bitlylogin = self.registryValue('bitlyLogin')
        self.bitlyapikey = self.registryValue('bitlyApiKey')
        # THESE NEED TO BE CHECKED TO MAKE SURE WE HAVE THEM.
        # displayLinkTitles displayImageTitles displayOtherTitles displayShortURL
        # DOMAIN-SPECIFIC PARSING. FORMAT IS: DOMAIN: FUNCTION
        self.domainparsers = {
            'vimeo.com': '_vimeotitle', # Vimeo
            'player.vimeo.com': '_vimeotitle',
            'm.youtube.com': '_yttitle', # YouTube
            'www.youtube.com': '_yttitle',
            'youtube.com': '_yttitle',
            'youtu.be': '_yttitle',
            'www.dailymotion.com': '_dmtitle', # Dailymotion
            'dailymotion.com': '_dmtitle',
            #'www.blip.tv': '_bliptitle',
            #'blip.tv': '_bliptitle',
            #'vine.co': '_vinetitle',
            'reddit.com': '_reddit', # Reddit
            'www.reddit.com': '_reddit',
            'myanimelist.net': '_mal', # My Anime List
            'www.myanimelist.net': '_mal',
            'instagram.com': '_instagram', # instagram
            'www.instagram.com': '_instagram',
            'open.spotify.com': '_spotify', # Spotify
            'play.spotify.com': '_spotify'
            }

    def die(self):
        self.__parent.die()

    ##############
    # FORMATTING #
    ##############

    def __get_age_str(self, published, use_fresh=True):
        now = datetime.now(tz=published.tzinfo)

        # Check if the publish date is in the future (upcoming episode)
        if published > now:
            age = published - now
            future = True
        else:
            age = now - published
            future = False

        secs = age.total_seconds()

        halfyears, days, hours, minutes = age.days // 182, age.days % 365, secs // 3600, secs // 60 % 60

        agestr = []
        years = halfyears * 0.5

        # uploaded TODAY, whoa.
        if years == 0 and days == 0 and use_fresh:
            return 'FRESH'

        if years >= 1:
            agestr.append("%gy" % years)
        # don't display days for videos older than 6 months
        if years < 1 and days > 0:
            agestr.append("%dd" % days)

        if not agestr:
            agestr.append('%dh' % hours)

        if not agestr:
            agestr.append('%dm' % minutes)

        if agestr:
            agestr.append(" from now" if future else " ago")

        return "".join(agestr)

    def _red(self, string):
        """Returns a red string."""
        return ircutils.mircColor(string, 'red')

    def _yellow(self, string):
        """Returns a yellow string."""
        return ircutils.mircColor(string, 'yellow')

    def _green(self, string):
        """Returns a green string."""
        return ircutils.mircColor(string, 'green')

    def _blue(self, string):
        """Returns a blue string."""
        return ircutils.mircColor(string, 'blue')

    def _bold(self, string):
        """Returns a bold string."""
        return ircutils.bold(string)

    def _ul(self, string):
        """Returns an underline string."""
        return ircutils.underline(string)

    def _bu(self, string):
        """Returns a bold/underline string."""
        return ircutils.bold(ircutils.underline(string))

    #########################
    # HTTP HELPER FUNCTIONS #
    #########################

    def _openurl(self, url, urlread=True, headers=None, useragent=None):
        """Generic http fetcher we can use here."""

        # Replace default useragent
        if useragent:
            self.headers = {'User-agent': useragent}

        # deal with headers first.
        h = self.headers
        # see if the user wants to add more.
        if headers:
            h = h.append(headers)

        # big try except block and error handling for each.
        self.log.info("_openurl: Trying to open: {0}".format(url))
        try:
            response = requests.get(url, stream=True, headers=h, timeout=5, allow_redirects=True)

            # should we just return text or the actual object.
            #  and int(response.headers.get('content-length')) < 10000000
            if response.headers['Content-Type'].startswith('video/'):
                return response
            elif response.headers['Content-Type'].startswith('audio/'):
                return response
            elif urlread:
                return response.text
            else:
                return response
        except Exception, e:
            self.log.info('_openurl: ERROR: Cannot open: {0} ERROR: {1} '.format(url, e))
            return None

    ####################
    # BASE62 FUNCTIONS #
    ####################

    def _base62encode(number):
        """Encode a number in base62 (all digits + a-z + A-Z)."""

        base62chars = string.digits + string.letters
        l = []
        while number > 0:
            remainder = number % 62
            number = number // 62
            l.insert(0, base62chars[remainder])
        return ''.join(l) or '0'

    def _base62decode(str_value):
        """Decode a base62 string (all digits + a-z + A-Z) to a number."""

        base62chars = string.digits + string.letters
        return sum([base62chars.index(char) * (62 ** (len(str_value) - index - 1)) for index, char in enumerate(str_value)])

    #############################
    # INTERNAL HELPER FUNCTIONS #
    #############################

    def _numfmt(self, num):
        """Format numbers into human readable units."""

        num = float(num)
        for x in ['','k','m','b']:
            if num < 1000.0 and num > -1000.0:
                return "%3.1f%s" % (num, x)
            num /= 1000.0

    def _sizefmt(self, num):
        """Format size information into human readable units."""

        if num is None:
            return 'No size'
        num = int(num)
        for x in ['b','KB','MB','GB']:
            if num < 1024.0:
                return "%3.1f%s" % (num, x)
            num /= 1024.0
        return "%3.1f%s" % (num, 'TB')

    def _cleantitle(self, msg):
        """Clean up the title of a URL."""

        cleaned = msg.translate(dict.fromkeys(range(32))).strip()
        return re.sub(r'\s+', ' ', cleaned)

    def _tidyurl(self, url):
        """Tidy up urls for prior to processing."""

        # do utf-8 stuff here? decode.
        url = re.sub('(.*)(http[^\s]*)', '\g<2>', url)
        return url

    def _tidydesc(self, desc):
        """Tidies up description string."""

        desc = desc.replace('\n', '').replace('\r', '')
        return desc

    # we'll need to support unicode urls..
    # http://stackoverflow.com/questions/4389572/how-to-fetch-a-non-ascii-url-with-python-urlopen
    # iri/uri https://docs.djangoproject.com/en/dev/ref/unicode/

    # iri2uri ripped from httplib2
    # https://code.google.com/p/httplib2/source/browse/python2/httplib2/iri2uri.py
    def encode(self, c):
        retval = c
        i = ord(c)
        # escape range.
        escape_range = [
            (0xA0, 0xD7FF),
            (0xE000, 0xF8FF),
            (0xF900, 0xFDCF),
            (0xFDF0, 0xFFEF),
            (0x10000, 0x1FFFD),
            (0x20000, 0x2FFFD),
            (0x30000, 0x3FFFD),
            (0x40000, 0x4FFFD),
            (0x50000, 0x5FFFD),
            (0x60000, 0x6FFFD),
            (0x70000, 0x7FFFD),
            (0x80000, 0x8FFFD),
            (0x90000, 0x9FFFD),
            (0xA0000, 0xAFFFD),
            (0xB0000, 0xBFFFD),
            (0xC0000, 0xCFFFD),
            (0xD0000, 0xDFFFD),
            (0xE1000, 0xEFFFD),
            (0xF0000, 0xFFFFD),
            (0x100000, 0x10FFFD),
        ]
        for low, high in escape_range:
            if i < low:
                break
            if i >= low and i <= high:
                retval = "".join(["%%%2X" % ord(o) for o in c.encode('utf-8')])
                break
        return retval

    def iri2uri(self, uri):
        """Convert an IRI to a URI. Note that IRIs must be
        passed in a unicode strings. That is, do not utf-8 encode
        the IRI before passing it into the function."""
        if isinstance(uri ,unicode):
            (scheme, authority, path, query, fragment) = urlsplit(uri)
            authority = authority.encode('idna')
            # For each character in 'ucschar' or 'iprivate'
            #  1. encode as utf-8
            #  2. then %-encode each octet of that utf-8
            uri = urlunsplit((scheme, authority, path, query, fragment))
            uri = "".join([self.encode(c) for c in uri])
        return uri

    def urlEncodeNonAscii(self, b):
        return re.sub('[\x80-\xFF]', lambda c: '%%%02x' % ord(c.group(0)), b)

    def iriToUri(self, iri):
        parts= urlparse(iri)
        return urlunparse(
            part.encode('idna') if parti==1 else urlEncodeNonAscii(part.encode('utf-8'))
            for parti, part in enumerate(parts)
        )


    #####################
    # LONGURL FUNCTIONS #
    #####################

    #def longurlservices(self, irc, msg, args):
    #    """
    #    Debug command to test longurl services.
    #    """
    #
    #    irc.reply("longurlcachetime: {0} NOW: {1}".format(self.longUrlCacheTime, time.time()))
    #    irc.reply("Services: {0}".format(self._getlongurlservices()))
    #
    #longurlservices = wrap(longurlservices)

#    def _getlongurlservices(self):
#        """Function to maintain list of shorturl services for resolving with cache."""
#
#        if self.longUrlServices and abs(time.time()-self.longUrlCacheTime) < 86400:
#            self.log.info("longurlservices: Just returning..")
#            # we have services and they're within the cache period.
#            return self.longUrlServices
#        else:
#            self.log.info("longurlservices: Fetching longurl services.")
#            url = 'http://api.longurl.org/v2/services?format=json'
#            lookup = self._openurl(url)
#            if not lookup:
#                self.log.error("longurlservices: could not fetch URL: {0}".format(url))
#                return None
#            # we did get a url. lets process.
#            try:
#                services = json.loads(lookup)
#                domains = [item for item in services]
#                self.longUrlCacheTime = time.time()
#                self.longUrlServices = domains
#                return domains
#            except Exception, e:
#                self.log.error("longurlservices: ERROR processing JSON in longurl services: {0}".format(e))
#                return None
#
#    def _longurl(self, surl):
#        """Resolve shortened urls into their long form."""
#
#        url = 'http://api.longurl.org/v2/expand?format=json&url=%s' % surl
#        lookup = self._openurl(url)
#        if not lookup:
#            self.log.error("_longurl: could not fetch: {0}".format(url))
#            return None
#        # we have a url, proceed with processing json.
#        try:
#            lookup = json.loads(lookup)
#            return lookup['long-url']
#        except Exception, e:
#            self.log.error("_longurl: json processing error: {0}".format(e))
#            return None

    ####################
    # BITLY SHORTENING #
    ####################

    def _shortenurl(self, url, type=False):
        """Shorten links via is.gd."""

        # don't reshorten bitly links.
        if urlparse(url).hostname in ('is.gd'):
            return url
        # otherwise, try to shorten links. uses api.
        apiurl = 'https://is.gd/create.php?format=json&url=%s' % (self.urlEncodeNonAscii(url))
        # fetch our url.
        lookup = self._openurl(apiurl)
        if not lookup:
            self.log.error("_shortenurl: could not fetch: {0}".format(url))
            return None
        # now try to parse json.
        try:
            data = json.loads(lookup)
            if type:
                return data['shorturl'] + ".jpg"
            else:
                return data['shorturl']
        except Exception, e:
            self.log.error("_shortenurl: error parsing JSON: {0}".format(e))
            return None

    ##################################
    # MAIN LOGIC FOR FETCHING TITLES #
    ##################################

    def _titledirector(self, url, gd=False, di=True):
        """Main logic for how to handle links."""

        domain = urlparse(url).hostname  # parse out domain.
        #self.log.info(url)
        # put a handler per domain(s)
        if domain in self.domainparsers:  # fetches the k (domain) v is function.
            parsemethod = getattr(self, self.domainparsers[domain])
            title = parsemethod(url)
            # if this breaks, should we resort to generic title fetching?
            if not title:  # make sure that we don't get description on these.
                title = self._fetchtitle(url, gd=False)
        else:  # we don't have a specific method so resort to generic title fetcher.
            title = self._fetchtitle(url, gd=gd, di=di)
        # now return the title.
        return title

    def _fetchtitle(self, url, gd=False, di=True):
        """Generic title fetcher for non-domain-specific titles."""

        #self.log.info("DI IS: {0}".format(di))
        # fetch the url.
        response = self._openurl(url, urlread=False)
        if not response:  # make sure we have a resposne.
            self.log.error("_fetchtitle: no response from: {0}".format(url))
            return None
        # parse content
        # now lets process the first 100k.
        content = response
        # before we do anything, lets figure out redirects.
        # we might need to handle it like here:
        # http://stackoverflow.com/questions/2318446/how-to-follow-meta-refreshes-in-python
        RE_REFRESH_TAG = re.compile(r'<meta[^>]+http-equiv\s*=\s*["\']*Refresh[^>]+', re.I)
        RE_REFRESH_URL = re.compile(r'url=["\']*([^\'"> ]+)', re.I)
        # now test. this can probably be improved below.
        if response.headers.get('content-type').startswith('text/'):
            match = RE_REFRESH_TAG.search(content.text)
            if match:
                match = RE_REFRESH_URL.search(match.group(0))
                if match:
                    newurl =  match.group(1)
                    # we're here if we did find a redirect.
                    response = self._openurl(newurl, urlread=False)
                    if not response:  # make sure we have a resposne.
                        self.log.error("_fetchtitle: no response from: {0} (Redirected from: {1})".format(url, newurl))
                        return None
                    else:  # copy the new (redirected url) back to the old url string.
                        url = newurl
                        content = response
        # now begin regular processing.
        # get the "charset" (encoding)
        charset = response.encoding
        # dictionary for the type and size of content if provided in the http header.
        contentdict = {}
        contentdict['type'] = response.headers.get('content-type')
        contentdict['size'] = response.headers.get('content-length')
        if not contentdict['size']:  # case if we don't get length back.
            contentdict['size'] = len(content.content)

        if contentdict['type'].startswith('video/'):
            return " Video Type: {0} - Video Size: {1}".format(self._bold(contentdict['type'].replace('video/', '')), self._bold(self._sizefmt(contentdict['size'])))
	elif contentdict['type'].startswith('audio/'):
            return " Audio Type: {0}".format(self._bold(contentdict['type'].replace('audio/', '')))
        # now, process various types of content here. Image->text->others.
        # determine if it's an image and process. di must also be True.
        elif contentdict['type'].startswith('image/'):
            return " Image Type: {0} - Image Size: {1}".format(self._bold(contentdict['type'].replace('image/', '')), self._bold(self._sizefmt(contentdict['size'])))
        # if it is text, we try to just scrape the title out.
        elif contentdict['type'].startswith('text/'):
            # wrap the whole thing because who the hell knows wtf will happen.
            try:  # try to parse w/BS + encode properly.
                soup = BeautifulSoup(content.text, from_encoding=charset)
                #soup = unicode(soup)  # convert html entities.
                #self.log.info("{0}".format(soup))
                title = self._cleantitle(soup.title.string)
                # should we also fetch description? We have specific things to NOT fetch.
                # bad extensions.
                badexts = ['.jpg', '.jpeg', '.gif', '.png', '.iso', '.exe']
                if __builtins__['any'](url.endswith(x) for x in badexts):
                    gd = False
                else:
                    baddomains = ['twitter.com', 'panoramio.com', 'kickass.to', 'tinypic.com', 'ebay.com', 'imgur.com', 'dropbox.com']
                    # bad domains.
                    urlhostname = urlparse(url).hostname
                    if __builtins__['any'](b in urlhostname for b in baddomains):
                        gd = False
                # check if we should "get description" (GD)
                if gd:
                    desc = soup.find('meta', {'name':'description'})
                    if desc:  # found a description. make sure content is in there.
                        #self.log.info("DESC IS: {0} TYPE: {1}".format(desc, type(desc)))
                        if desc.get('content'):
                            #self.log.info("We're returning with content")
                            return {'title': title.encode('utf-8', 'ignore'), 'desc': self._tidydesc(desc['content'].encode('utf-8', 'ignore')) }
                        else:
                            #self.log.info("Not returning with content.")
                            return title.encode('utf-8', 'ignore')
                    else:  # didn't find desc.
                        #self.log.info("Didnd't find desc")
                        return title.encode('utf-8', 'ignore')
                else:  # don't want description, just title.
                    return title.encode('utf-8', 'ignore')
                # now take the the possible 'title and 'gd' above and return.
            except Exception, e:
                self.log.error("_fetchtitle: ERROR: Could not parse title of: {0} - {1}".format(url, e))
                return None
        # handle any other filetype using libmagic.
        else:
            try:  # we could also incorporate some type of metadata attempt via exif here.
                #typeoffile = magic.from_buffer(content.content)
                return "Content type: {0} - Size: {1}".format(self._bold(contentdict['type']), self._bold(self._sizefmt(contentdict['size'])))
            except Exception, e:  # give a detailed error here in the logs.
                self.log.error("ERROR: _fetchtitle: error trying to parse {0} via other (else) :: {1}".format(url, e))
                self.log.error("ERROR: _fetchtitle: no handler for {0} at {1}".format(contentdict['type'], url))
                return None

    ##############
    # MAIN LOGIC #
    ##############

    def _titler(self, url, channel):
        """This calls the title and url parts of our plugin."""

        # first, we need to figure out user options on what to call.
        if self.registryValue('displayURL', channel):
            displayURL = True
        else:
            displayURL = False
        # shorten URL?
        if self.registryValue('displayShortURL', channel):
            fetchShortURL = True
        else:
            fetchShortURL = False
        # display desc?
        if self.registryValue('displayDescriptionIfText', channel):
            displayDesc = True
        else:
            displayDesc = False
        # display image titles if someone pastes a jpg?
        if self.registryValue('displayImageTitles', channel):
            displayImages = True
        else:
            displayImages = False
        # now, work with the above strings and make our calls.
        # before we do anything, if a "shortened" link is pasted, we have to "expand" (or try) it.
        domain = urlparse(url).hostname  # parse out domain.
        # first, check if our link is inside a shortener. fetch real url.
        # if domain in self.longUrlServices:
        #     expandedurl = self._longurl(url)  # try to expand it back to normal.
        #     if expandedurl:  # we got the expanded url back.
        #         url = expandedurl  # use the realurl.
        # we always want the title. gd will be handled separately.
        if displayDesc:  # this will return a dict vs a string. we need to handle this properly below.
            title = self._titledirector(url, gd=True, di=displayImages)
            # now, due to how _fetchtitle works, we don't know how the string will return
            # due to URL content. we prepare an instance below.
            if isinstance(title, dict):  # we got a dict back.
                #self.log.info("title brought back: {0}".format(title))
                if 'desc' in title:
                    desc = title['desc']
                else:
                    self.log.info("_titledirector: Could not find meta-description content for: {0}".format(url))
                    desc = None
                # now set title. desc set above.
                title = title['title']
                # but, lets also check if desc == title so we don't spew dupes.
                if title and desc and title == desc:  # they match strings.
                    desc = None
            else:  # didn't get a dict back so no desc.
                desc = None
            # did not get a dict back.
        else:  # don't display description.
            title, desc = self._titledirector(url, gd=False, di=displayImages), None

        # now work wit hthe urls.
        if displayURL:
            # first, we determine if we can find the title.
            # next, we have to see what the user/channel wants to display.
            if fetchShortURL:
                #self.log.info("fetchshorturl")
                shorturl = self._shortenurl(url)
                if not shorturl:  # no shorturl.  lets check the title
                    #self.log.info("fetchshorturl/not outurl")
                    if title:
                        o = "{0} - {1}".format(url, title)
                    else:  # no shorturl. no title. don't return anything.
                        o = None
                else:  # we got the shorturl.
                    if title:  # we have title + shorturl.
                        o = "{0} - {1}".format(shorturl, title)
                    else:  # we have shorturl but no title.
                        o = "{0}".format(shorturl)
            else:  # we don't want the short url but want full. lets check if we have title.
                #self.log.info("not fetchshorturl")
                if title:  # display full url + title.
                    o = "{0} - {1}".format(url, title)
                else:  # don't just repeat displaying the url because we didn't get the title.
                    o = None
        else:  # we only want the title.
            #self.log.info("not displayurl")
            if not title:  # however, if we don't want url+no title, why return a thing?
                o = None
            else:
                o = "{0}".format(title)
        # IDEA: should we have a config to limit max title length?
        # now, lets figure out how to return, based on gd.
        #self.log.info("TITLE IS: {0} O IS: {1} DESC IS: {2}".format(title, o, desc))
        if desc:  # we have a description.
            return {'title': o, 'desc': desc}
        else:  # no gd. just return a string.
            return o

    ############################################
    # MAIN TRIGGER FOR URLS PASTED IN CHANNELS #
    ############################################

    def doPrivmsg(self, irc, msg):
        channel = msg.args[0]  # channel, if any.
        # user = msg.nick  # nick of user.
        # linkdb = LinkDB()   # disable for now.
        # linkdb.add(url, title, channel, user)

        # first, check if we should be 'disabled' in this channel.
        if self.registryValue('disableChannel', channel):
            return
        # don't react to non-ACTION based messages.
        if ircmsgs.isCtcp(msg) and not ircmsgs.isAction(msg):
            return
        if irc.isChannel(channel):  # must be in channel.
            if ircmsgs.isAction(msg):  # if in action, remove.
                text = ircmsgs.unAction(msg)
            else:
                text = msg.args[1]

            # NSFW search
            if re.search("nsfw", text, re.IGNORECASE):
                nsfw = self._red("^ NSFW ")
            else:
                nsfw = ""

            # find all urls pasted.
            #urlpattern = """(((http|ftp|https|ftps|sftp)://)|(www\.))+(([a-zA-Z
            #                0-9\._-]+\.[a-zA-Z]{2,6})|([0-9]{1,3}\.[0-9]{1,3}\.
            #                [0-9]{1,3}\.[0-9]{1,3}))(/[a-zA-Z0-9\&amp;%_\./-~-]*)?"""
            # extracts links in text and stores in an iterator.
            #matches = re.finditer(urlpattern, text.strip(), re.IGNORECASE + re.VERBOSE)
            for url in utils.web.urlRe.findall(text):
            #for url in matches:
                self.log.info("FOUND URL: {0}".format(url))
                url = url.decode('utf-8')
                url = unicode(url)
                url = self.iri2uri(url)
                self.log.info("after iri to uri: {0}".format(url))
                # url = self._tidyurl(url)  # should we tidy them?
                output = self._titler(url, channel)
                # now, with gd, we must check what output is.
                if output:  # if we did not get None back.
                    if isinstance(output, dict):  # came back a dict.
                        # output.
                        if 'desc' in output and 'title' in output and output['desc'] is not None and output['title'] is not None:
                            irc.sendMsg(ircmsgs.privmsg(channel, nsfw + self._bold("[title]") + " {0}".format(output['title'])))
                            irc.sendMsg(ircmsgs.privmsg(channel, nsfw + self._bold("[title]") + " {0}".format(output['desc'])))
                        elif 'title' in output and output['title'] is not None:
                            irc.sendMsg(ircmsgs.privmsg(channel, nsfw + self._bold("[title]") + " {0}".format(output['title'])))
                    else:  # no desc.
                        #irc.sendMsg(ircmsgs.privmsg(channel, "{0} {1}"., format(output)))
                        irc.sendMsg(ircmsgs.privmsg(channel, nsfw + self._bold("[title]") + " {0}".format(output)))


    #####################################################
    # PUBLIC/PRIVATE TRIGGER, MAINLY USED FOR DEBUGGING #
    #####################################################

    def titler(self, irc, msg, args, opturl):
        """<url>

        Public test function for Titler.
        Ex: http://www.google.com
        """

        channel = msg.args[0]

        # clean up opturl
        opturl = opturl.decode('utf-8')
        opturl = unicode(opturl)
        opturl = self.iri2uri(opturl)

        # main.
        output = self._titler(opturl, channel)
        # now, with gd, we must check what output is.
        if output:  # if we did not get None back.
            if isinstance(output, dict):  # came back a dict.
                if 'title' in output:  # we got a title back.
                    irc.reply("TITLE: {0}".format(output['title']))
                    if 'desc' in output:
                        irc.reply("GD: {0}".format(output['desc']))
            else:
             	irc.reply("Response: {0}".format(output))

    titler = wrap(titler, [('text')])

    #######################################
    # INDIVIDUAL DOMAIN PARSERS WITH API  #
    # (SEE README FOR HOW TO CODE MORE)   #
    #######################################

    def _reddit(self, url):
        """Handle finding titles for reddit links."""

        # REDDIT THREAD
        # http://www.reddit.com/r/whowouldwininafight/comments/1rwrff/walter_white_breaking_bad_vs_jax_teller_sons_of/
        # http://api.reddit.com/r/whowouldwininafight/comments/1rwrff.json
        # USERNAMES
        # http://www.reddit.com/user/Darthdre758
        # http://api.reddit.com/user/Darthdre758/about.json
        # SUBREDDIT
        # http://api.reddit.com/r/subreddit/about.json
        # http://www.reddit.com/r/Sonsofanarchy/
        # COMMENT?
        # http://www.reddit.com/r/Sonsofanarchy/comments/1s29lh/spoiler_something_i_noticed_during_tonights/cdt7869
        pathname = urlparse(url).path
        # make sure we have a pathname.
        if not pathname or pathname == '/' or pathname == '':
            self.log.error("_reddit: ERROR: could not determine pathname from: {0}".format(url))
            return None
        else:  # strip the leading /.
            pathname = pathname[1:]
            if pathname.endswith('/'):  # also remove tailing if present.
                pathname = pathname[:-1]
        # now, lets break it up by /
        pathnamesplit = pathname.split('/')
        # determine what api/calls to use now based on this. instead of a general api handler, we do a call from within.
        #self.log.info("PATHNANMESPLIT: {0}".format(pathnamesplit))
        if pathnamesplit[0] == "r":  # subreddit or comment to one.
            subreddit = pathnamesplit[1]
            # now, we must determine if a user pasted a subreddit or a link to a post within one.
            if len(pathnamesplit) == 2:  # top level subreddit.
                # make our api call now.
                apiurl = 'http://api.reddit.com/r/%s/about.json' % subreddit
                lookup = self._openurl(apiurl)
                if not lookup:
                    self.log.error("_reddit: could not fetch API URL: {0}".format(apiurl))
                    return None
                # process the json.
                try:
                    data = json.loads(lookup)
                    # self.log.info("DATA: {0}".format(data))
                    title = data['data']['title']  # str.
                    desc = data['data']['public_description']  # str.
                    subs = self._numfmt(data['data']['subscribers'])  # int.
                    created = data['data']['created_utc']
                    created2 = self.__get_age_str(datetime.fromtimestamp(created))
                    o = "subreddit: {0} - {1} - subscribers: {2} - created: {3}".format(title, desc, subs, created2)
                    return o
                except Exception, e:  # something broke.
                    self.log.error("_reddit: could not process JSON from API URL: {0} :: {1}".format(apiurl, e))
                    return None
            elif len(pathnamesplit) == 5:  # direct comment to.
                commentid = pathnamesplit[3]
                # make our api call now.
                apiurl = 'http://api.reddit.com/r/%s/comments/%s.json' % (subreddit, commentid)
                lookup = self._openurl(apiurl)
                if not lookup:
                    self.log.error("_reddit: could not fetch API URL: {0}".format(apiurl))
                    return None
                # process the json.
                try:
                    data = json.loads(lookup)
                    #author = data['data']['children'][0]['data']['author']
                    title = data[0]['data']['children'][0]['data']['title']
                    comments = self._numfmt(data[0]['data']['children'][0]['data']['num_comments'])
                    score = data[0]['data']['children'][0]['data']['score']
                    created  = data[0]['data']['children'][0]['data']['created_utc']
                    created2 = self.__get_age_str(datetime.fromtimestamp(created))
                    o = "subreddit: {0} - {1} - comments: {2} - score: {3} - created: {4}".format(subreddit, title, comments, score, created2)
                    return o
                except Exception, e:  # something broke.
                    self.log.error("_reddit: could not process JSON from API URL: {0} :: {1}".format(apiurl, e))
                    return None
            else:  # can't determine.
                self.log.error("_reddit: ERROR: could not determine what API to use from: {0}".format(url))
                return None
        elif pathnamesplit[0] == "user":  # someone pasted a link to a user.
            username = pathnamesplit[1]
            # make our api call now.
            apiurl = 'http://api.reddit.com/user/%s/about.json' % username
            lookup = self._openurl(apiurl)
            if not lookup:
                self.log.error("_reddit: could not fetch API URL: {0}".format(apiurl))
                return None
            # process the json.
            try:
                data = json.loads(lookup)
                created = data['data']['created_utc']  # UTC.
                created2 = self.__get_age_str(datetime.fromtimestamp(created))
                linkkarma = data['data']['link_karma']  # int.
                commentkarma = data['data']['comment_karma']  # int.
                # is_mod = data['data']['is_mod']
                o = "reddit user: {0} KARMA: link {1}/comment {2} signed up: {3}".format(username, linkkarma, commentkarma, created2)
                return o
            except Exception, e:  # something broke.
                self.log.error("_reddit: could not process JSON from API URL: {0} :: {1}".format(apiurl, e))
                return None
        else:  # we don't have a condition for this url. return None.
            self.log.error("_reddit: ERROR: could not determine what API to use from: {0}".format(url))
            return None

    def _vinetitle(self, url):
        """Fetch information about vine videos."""

        # <meta property="twitter:title" content="Only The Best Vines's post on Vine">
        # <meta property="twitter:description" content="Did that really just happen?!?😂😂">
        # we do a regular http fetch here.
        lookup = self._openurl(url)
        if not lookup:
            self.log.error("_vinetitle: could not fetch: {0}".format(url))
            return None
        # soup it.
        soup = BeautifulSoup(lookup)
        desc = soup.find('meta', {'property':'twitter:description'})
        if not desc:
            return None
        else:
            if desc.get('content'):
                o = desc['content'].encode('utf-8', 'ignore')
            else:
                return None
        # now return.
        return "Vine Video: {0}".format(o)

    def _instagram(self, url):
        """Fetch information for instagram"""

        apiurl = 'https://api.instagram.com/oembed/?callback=&hl=en&url=%s' % (self.urlEncodeNonAscii(url))
        lookup = self._openurl(apiurl)
        if not lookup:
            self.log.error("_instagram: could not fetch API URL: {0}".format(apiurl))
            return None
        # process the json.
        try:
            data = json.loads(lookup)
            thumbnail_url = data["thumbnail_url"]
            html = data["html"]
            username = data["author_name"]
            directurl_shortened = self._shortenurl(thumbnail_url, True)

            RE_DATETIME = re.compile(r'datetime=["\']*([^\' "> ]+)', re.I)
            match = RE_DATETIME.search(html)

            publish_datetime = parse_datetime(match.group(1))


            o = "Instagram: Photo by {0} on {1} | Direct url: {2}".format(self._bold(username.encode('utf-8', 'replace')), self._bold(publish_datetime.strftime("%Y-%m-%d %H:%M %z")), directurl_shortened)
            return o
        except Exception, e:
            self.log.error("_instagram: error processing JSON: {0}".format(e))
            return None


    def _bliptitle(self, url):
        """Fetch information for blip.tv"""

        # http://blip.tv/the-gauntlet/the-gauntlet-season-2-episode-8-6693719?skin=json
        query = urlparse(url)
        pathname = query.path
        # make sure we have a pathname.
        if not pathname or pathname == '/' or pathname == '':
            self.log.error("_bliptitle: ERROR: could not determine pathname from: {0}".format(url))
            return None
        # blip seems to be smart and requires you to just append ?skin=json.
        apiurl = '%s?skin=json' % url
        lookup = self._openurl(apiurl)
        if not lookup:
            self.log.error("_dmtitle: could not fetch: {0}".format(url))
            return None
        # try and parse json.
        try:
            # we must cleanup the JSONP part.
            lookup = lookup.replace(']);', '').replace('blip_ws_results([', '')
            data = json.loads(lookup)
            title = data['Post']['title']
            width = data['Post']['media']['width']
            height = data['Post']['media']['height']
            desc = data['Post']['description']
            posted = desc = data['Post']['datestamp']
            o = "{0} Desc: {1} Size: {2}x{3} Posted: {4}".format(title, desc, width, height, posted)
            return o
        except Exception, e:
            self.log.error("_bliptitle: ERROR processing JSON: {0}".format(e))
            return None

    def _dmtitle(self, url):
        """Fetch information about dailymotion videos."""

        # http://www.dailymotion.com/video/xsxgyh_eclectic-method-bill-murray_fun
        query = urlparse(url)
        pathname = query.path
        # make sure we have a pathname.
        if not pathname or pathname == '':
            self.log.error("_dmtitle: ERROR: could not determine pathname from: {0}".format(url))
            return None
        else:  # pathname worked so lets remove the first char '/'
            pathname = pathname[1:]
        # now lets parse by the '/'
        pathnamesplit = pathname.split('/')  # split on /
        pathnamelen = len(pathnamesplit)  # len of such.
        # check for most popular urls.
        if pathnamelen == 2 and pathnamesplit[0] == "video":
            urlid = pathnamesplit[1]
        else:
            self.log.error("_dmtitle: ERROR: could not determine videoid from url: {0}".format(url))
            return None
        # we have dmid. lets fetch their json.
        apiurl = 'https://api.dailymotion.com/video/%s?fields=title,duration,description,explicit,language,rating' % urlid
        lookup = self._openurl(apiurl)
        if not lookup:
            self.log.error("_dmtitle: could not fetch: {0}".format(url))
            return None
        # try and parse json.
        try:
            data = json.loads(lookup)
            title = data['title']
            dur = "%dm%ds" % divmod(data['duration'], 60)
            desc = utils.web.htmlToText(data['description'])
            explicit = data['explicit']
            lang = data['language']
            rating = data['rating']
            if rating == None:
                rating = "-"
            o = "DailyMotion Video: {0} | Duration: {1} | Explicit: {2} | Lang: {3} | Rating: {4}/5".format(self._bold(title), self._bold(dur), self._bold(explicit), self._bold(lang), self._bold(rating))
            return o
        except Exception, e:
            self.log.error("_dmtitle: ERROR processing JSON: {0}".format(e))
            return None

    def _vimeotitle(self, url):
        """Fetch information about vimeo videos from API."""

        # first, we have to parse the vimeo url because of how they do video ids.
        query = urlparse(url)
        if query.hostname == 'vimeo.com':
            if query.path.startswith('/m/'):
                videoid = query.path.split('/')[2]
            else:
                videoid = query.path.split('/')[1]
        elif query.hostname == 'player.vimeo.com':
            if query.path.startswith('/video/'):
                videoid = query.path.split('/')[2]
        else:
            videoid = None
        # now check and make sure we have it and its valid.
        if not videoid or not videoid.isdigit():
            self.log.error("_vimeotitle: Could not parse vimeo id from url: {0}".format(url))
            return None
        # try loading vimeo api
        vimeourl = 'http://vimeo.com/api/v2/video/%s.json' % videoid
        lookup = self._openurl(vimeourl)
        if not lookup:
            self.log.error("_vimeotitle: could not fetch: {0}".format(url))
            return None
        try:
            data = json.loads(lookup)
            return "Vimeo Video: {0} Size: {1}x{2} Duration: {3}".format(data['title'], data['width'], data['height'],("%dm%ds"%divmod(data['duration'],60)))
        except Exception, e:
            self.log.error("_vimeotitle: ERROR parsing JSON: {0}".format(e))
            return None

    def _spotify(self, url):
        """Fetch information about tracks, artists etc from Spotify Web API."""

        # first, we have to parse the vimeo url because of how they do video ids.
        query = urlparse(url)
        type = ""
        id = ""

        if query.path.startswith('/album/'):
            id = query.path.split('/')[2]
            type = "albums"
        elif query.path.startswith('/track/'):
            id = query.path.split('/')[2]
            type = "tracks"
        elif query.path.startswith('/artist/'):
            id = query.path.split('/')[2]
            type = "artists"
        else:
            self.log.error("_spotify: ERROR: could not determine id and type from: {0}".format(url))
            return None

        # now that we have our imgurid, lets look it up.
        apiurl = 'https://api.spotify.com/v1/%s/%s' % (type, id)
        # fetch our url.
        lookup = self._openurl(apiurl)
        if not lookup:
            self.log.error("_spotify: could not fetch API url: {0}".format(apiurl))
            return None

        try:
            data = json.loads(lookup)

            title = ""

            # For tracks and albums, add artists
            if type in ['albums', 'tracks']:
                artists = []
                for artist in data['artists']:
                    artists.append(artist['name'].encode('utf-8', 'ignore'))
                title += ', '.join(artists)

            if type == "albums":
                title += ' - %s (Released: %s)' % (self._bold(data['name'].encode('utf-8', 'ignore')), self._bold(data['release_date']))
            elif type == "artists":
                title += data['name']
                genres_n = len(data['genres'])
                if genres_n > 0:
                    genitive = 's' if genres_n > 1 else ''
                    genres = data['genres'][0:4]
                    more = ' +%s more' % genres_n - 5 if genres_n > 4 else ''
                    title += ' (Genre%s: %s%s)' % (genitive, ', '.join(genres), more)
            elif type == "tracks":
                miliseconds = data['duration_ms']
                hours, milliseconds = divmod(miliseconds, 3600000)
                minutes, milliseconds = divmod(miliseconds, 60000)
                seconds = float(milliseconds) / 1000
                title += ' - %s - %s (%d:%d)' % (self._bold(data['album']['name'].encode('utf-8', 'ignore')), data['name'].encode('utf-8', 'ignore'), minutes, seconds)

            return title
        except Exception, e:
            self.log.error("_mal: ERROR processing JSON: {0}".format(e))
            return None

    def _mal(self, url):
        """Fetch information about anime, manga from Atarashii API."""

        # first, we have to parse the vimeo url because of how they do video ids.
        query = urlparse(url)
        type = ""
        id = ""

        if query.path.startswith('/anime/'):
            id = query.path.split('/')[2]
            type = "anime"
        elif query.path.startswith('/manga/'):
            id = query.path.split('/')[2]
            type = "manga"
        else:
            self.log.error("_mal: ERROR: could not determine id and type from: {0}".format(url))
            return None

        # now that we have our imgurid, lets look it up.
        apiurl = 'https://api.atarashiiapp.com/2/%s/%s' % (type, id)
        # fetch our url.
        lookup = self._openurl(apiurl, useragent="Atarashii! (Linux; Android 1;1 Build/1)")
        if not lookup:
            self.log.error("_mal: could not fetch API url: {0}".format(apiurl))
            return None
        # now lets process the json.
        try:
            data = json.loads(lookup)

            if type == "anime":
                title = data['title']
                anime_type = data['type']
                rating = data['members_score']
                favs = data['favorited_count']
                mem = data['members_count']
                started = data['start_date']

                try:
                    ended = data['end_date']
                    o = "{0} ({1}) - Rating: {2} - Favorited by {3} users - Watched by {4} users - Aired between {5} and {6}".format(title.encode('utf-8', 'replace'), self._bold(anime_type), self._bold(rating), self._bold(favs), self._bold(mem), self._bold(started), self._bold(ended))
                except:
                    o = "{0} ({1}) - Rating: {2} - Favorited by {3} users - Watched by {4} users - Released {5}".format(title.encode('utf-8', 'replace'), self._bold(anime_type), self._bold(rating), self._bold(favs), self._bold(mem), self._bold(started))

                return o

            elif type == "manga":
                title = data['title']
                anime_type = data['type']
                rating = data['members_score']
                favs = data['favorited_count']
                mem = data['members_count']
                status = data['status'].lower()

                # Make status prettier
                if status == "publishing":
                    status = "still being published"

                try:
                    chapters = data['chapters']
                    volumes = data['volumes']

                    o = "{0} ({1} - {2}) - Rating: {3} - Favorited by {4} users - Read by {5} users - {6} chapters, {7} volumes".format(title, self._bold(anime_type), self._bold(status), self._bold(rating), self._bold(favs), self._bold(mem), self._bold(chapters), self._bold(volumes))
                except:
                    o = "{0} ({1} - {2}) - Rating: {3} - Favorited by {4} users - Read by {5} users".format(title, self._bold(anime_type), self._bold(status), self._bold(rating), self._bold(favs), self._bold(mem))

                return o

        except Exception, e:
            self.log.error("_mal: ERROR processing JSON: {0}".format(e))
            return None

    def _yttitle(self, url):
        """Try and fetch youtube video information."""

        query = urlparse(url)
        if query.hostname == 'youtu.be':
            videoid = query.path[1:]
        elif query.hostname in ('www.youtube.com', 'youtube.com'):
            if query.path == '/watch':
                videoid = parse_qs(query.query)['v'][0]
            elif query.path[:7] == '/embed/':
                videoid = query.path.split('/')[2]
            elif query.path[:3] == '/v/':
                videoid = query.path.split('/')[2]
            else:
                videoid = None
        elif query.hostname == "m.youtube.com":
            if query.path == "/details":
                videoid = parse_qs(query.query)['v'][0]
            else:
                videoid = None
        else:
            videoid = None

        # for cases w/o a video ID like feeds or www.youtube.com
        if not videoid:
            self.log.error("_yttitle: ERROR: Could not parse videoid from url: {0}".format(url))
            return None
        # we have video id. lets fetch via gdata.
        gdataurl =  'https://www.googleapis.com/youtube/v3/videos?id=%s&part=snippet,statistics,contentDetails&key=AIzaSyDGKy6iPRXxG-EkM8XiOqjji4iVhK-UgDo' % videoid
        lookup = self._openurl(gdataurl)
        if not lookup:
            self.log.error("_yttitle: could not fetch: {0}".format(url))
            return None
        # we have our stuff back. try and parse json.
        try:
            data = json.loads(lookup)

            # check for errors.
            if 'error' in data:
                self.log.error("_yttitle: ERROR: {0} trying to fetch {1}".format(data['error']['message'], gdataurl))
                return None

            if len(data['items']) < 1:
                self.log.error("_yttitle: ERROR: no returned items for {0}".format(gdataurl))
                return None

            # data
            data = data['items'][0]

            # no errors. process json.
            title = data['snippet']['title']
            viewCount = data['statistics']['viewCount']
            duration = data['contentDetails']['duration'][2:].lower()
            if viewCount:
                viewCount = self._numfmt(viewCount)

            published = data['snippet']['publishedAt']
            published = datetime.strptime(published, "%Y-%m-%dT%H:%M:%S.%fZ")
            agestr = self.__get_age_str(published)

            rating = data['contentDetails'].get('contentRating', None)
            if rating:
                rating = rating.get('ytRating', None)

            if rating and rating == 'ytAgeRestricted':
                agerestricted = " | Age restricted"
            else:
                agerestricted = ""

            o = "YouTube: {0} | Views: {1} | Duration: {2} | Published: {3}{4}".format(self._bold(title.encode('utf-8', 'replace')), self._bold(viewCount), self._bold(duration), self._bold(agestr), agerestricted)
            return o
        except Exception, e:
            self.log.error("_yttitle: error processing JSON: {0}".format(e))
            return None




Class = Titler


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=250:
