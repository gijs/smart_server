"""
Utilities for Indivo

Ben Adida
ben.adida@childrens.harvard.edu
"""

from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.template import Context, loader
from django.conf import settings
from django import http
from django.utils import simplejson

from xml.dom import minidom
import libxml2, libxslt

from django.forms.fields import email_re
import django.core.mail as mail
import logging
import string, random
import functools

from rdflib import ConjunctiveGraph, Namespace, Literal
from StringIO import StringIO
import psycopg2
import psycopg2.extras

# taken from pointy-stick.com with some modifications
class MethodDispatcher(object):
  def __init__(self, method_map):
    self.methods= method_map

  def resolve(self, request):
    view_func = self.methods.get(request.method, None)
    return view_func

  def __call__(self, request, *args, **kwargs):
    view_func = self.resolve(request)
    if view_func:
      return view_func(request, *args, **kwargs)
    return http.HttpResponseNotAllowed(self.methods.keys())

def is_valid_email(email):
  return True if email_re.match(email) else False

def random_string(length, choices=[string.letters]):
  # FIXME: seed!
  return "".join([random.choice(''.join(choices)) for i in xrange(length)])

def send_mail(subject, body, sender, recipient):
  # if send mail?
  if settings.SEND_MAIL:
    mail.send_mail(subject, body, sender, recipient)
  else:
    logging.debug("send_mail to set to false, would have sent email to %s" % recipient)

def render_template_raw(template_name, vars, type='xml'):
  t_obj = loader.get_template('%s.%s' % (template_name, type))
  c_obj = Context(vars)
  return t_obj.render(c_obj)

def render_template(template_name, vars, type='xml'):
  content = render_template_raw(template_name, vars, type)

  mimetype = 'text/plain'
  if type == 'xml':
    mimetype = "application/xml"
  elif type == "json":
    mimetype = 'text/json'
  return HttpResponse(content, mimetype=mimetype)


def get_element_value(dom, el):
  try:
    return dom.getElementsByTagName(el)[0].firstChild.nodeValue
  except:
    return ""

def url_interpolate(url_template, vars):
  """Interpolate a URL template
  
  TODO: security review this
  """

  result_url = url_template

  # go through the vars and replace
  for var_name in vars.keys():
    result_url = result_url.replace("{%s}" % var_name, vars[var_name])

  return result_url

def is_browser(request):
  """Determine if the request accepts text/html, in which case
     it's a user at a browser.
  """
  accept_header = request.META.get('HTTP_ACCEPT', False) or request.META.get('ACCEPT', False)
  if accept_header and isinstance(accept_header, str):
    return "text/html" in accept_header.split(',')
  return False

def get_content_type(request):
  content_type = None
  if request.META.has_key('CONTENT_TYPE'):
    content_type = request.META['CONTENT_TYPE']
  if not content_type and request.META.has_key('HTTP_CONTENT_TYPE'):
    content_type = request.META['HTTP_CONTENT_TYPE']
  return content_type

# some decorators to make life easier
def django_json(func):
  def func_with_json_conversion(*args, **kwargs):
    return_value = func(*args, **kwargs)
    return HttpResponse(simplejson.dumps(return_value), mimetype='text/plain')
  functools.update_wrapper(func_with_json_conversion, func)
  return func_with_json_conversion

def apply_xslt(sourceDOM, stylesheetDOM):
    style = libxslt.parseStylesheetDoc(stylesheetDOM)
    return style.applyStylesheet(sourceDOM, None).serialize()

def xslt_ccr_to_rdf(source):
    sourceDOM = libxml2.parseDoc(source)
    ss = "%s%s"%(settings.XSLT_STYLESHEET_LOC, "ccr_to_rdf.xslt")
    ssDOM = libxml2.parseFile(ss)
    return apply_xslt(sourceDOM, ssDOM)


def meds_as_rdf(raw_xml):
    transformed = xslt_ccr_to_rdf(raw_xml)
   
    g = ConjunctiveGraph()
    g.parse(StringIO(transformed))
    
    rxcui_ids = [r[0].decode().split('/')[-1] 
                 for r in 
                    g.query("""SELECT ?cui_id  
                               WHERE {
                               ?med rdf:type med:medication .
                               ?med med:drug ?cui_id .
                               }""", 
                               initNs=dict(g.namespaces()))]
    for rxcui_id in rxcui_ids:
        try:
            print "ADDING", rxcui_id
            rxn_related(rxcui_id=int(rxcui_id), graph=g)
        except ValueError:
            pass
    return g.serialize()

def rxn_related(rxcui_id, graph):
   rxcui_id = str(rxcui_id)
   dcterms = Namespace('http://purl.org/rss/1.0/modules/dcterms/', "dctems")
   med = Namespace('http://smartplatforms.org/med#')
   rdf = Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')
   rxn=Namespace("http://link.informatics.stonybrook.edu/rxnorm/")
   rxcui=Namespace( "http://link.informatics.stonybrook.edu/rxnorm/RXCUI/")
   rxaui=Namespace("http://link.informatics.stonybrook.edu/rxnorm/RXAUI/")
   rxatn=Namespace("http://link.informatics.stonybrook.edu/rxnorm/RXATN#")
   rxrel=Namespace("http://link.informatics.stonybrook.edu/rxnorm/REL#")

   graph.bind("rxn", rxn)
   graph.bind("rxaui", rxaui)
   graph.bind("rxrel", rxrel)
   graph.bind("rxatn", rxatn)
   graph.bind("rxcui", rxcui)
   
   conn=psycopg2.connect("dbname='%s' user='%s' password='%s'"%
                             (settings.DATABASE_RXN, 
                              settings.DATABASE_USER, 
                              settings.DATABASE_PASSWORD))
   
   cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
   
   q = """select distinct rxcui, rxaui, atn, atv from 
       rxnsat where rxcui=%s and suppress='N' and 
       atn != 'NDC' order by rxaui, atn;"""
  
   print q%rxcui_id
  
   cur.execute(q, (rxcui_id,))
   
   rows = cur.fetchall()
   
   graph.add((rxcui[rxcui_id], rdf['type'], rxcui))
   for row in rows:
       atn = row['atn'].replace(" ", "_")
       graph.add((rxcui['%s'%row['rxcui']], rxn['has_RXAUI'], rxaui[row['rxaui']] ))
       graph.add((rxaui[row['rxaui']], rxatn[atn], Literal(row['atv']) ))

   q = """select min(rela) as rela, min(str) as str from 
           rxnrel r join rxnconso c on r.rxcui1=c.rxcui 
           where rxcui2=%s group by rela;"""
   print q%rxcui_id
   
   cur.execute(q, (rxcui_id,))
   
   rows = cur.fetchall()
   for row in rows:
       graph.add((rxcui[rxcui_id], rxrel[row['rela']], Literal(row['str']) ))

   return

def strip_ns(target, ns):
    return str(target.uri).split(ns)[1]
