#!/usr/bin/python

# Requires JSON Merge library
# wget https://github.com/avian2/jsonmerge/archive/master.zip
# unzip master.zip
# cd jsonmerge-master
# python setup.py install

# Also requires ldif.py in same folder

import os
import os.path
import shutil
import sys
import time
import traceback
from ldif import LDIFParser, CreateLDIF
from jsonmerge import merge
import base64
import json

ldif_folder = None

# Old Config
appliance_config_old_fn = "%s/appliance.ldif" % ldif_folder
organization_config_old_fn = "%s/organization.ldif" % ldif_folder
oxAuth_config_old_fn = "%s/oxauth_config.ldif" % ldif_folder
oxTrust_config_old_fn = "%s/oxtrust_config.ldif" % ldif_folder
# New config
appliance_config_new_fn = "%s/appliance_config_new.ldif" % ouputFolder
organization_config_new_fn = "%s/organization_config_new.ldif" % ouputFolder
oxAuth_config_new_fn = "%s/oxauth_config_new.ldif" % ouputFolder
oxTrust_config_new_fn = "%s/oxtrust_config_new.ldif" % ouputFolder

log = "./import23.log"
logError = "./import23.error"
ouputFolder = "./output_ldif"
configLdifFolder = "%s/config" % outputFolder
password_file = "/root/.pw"

service = "/usr/sbin/service"
ldapmodify = "/opt/opendj/bin/ldapmodify"
ldapsearch = "/opt/opendj/bin/ldapsearch"
ldapdelete = "/opt/opendj/bin/ldapdelete"

ignore_files = ['101-ox.ldif',
                'gluuImportPerson.properties',
                'oxTrust.properties',
                'oxauth-config.xml',
                'oxauth-errors.json',
                'oxauth.config.reload',
                'oxauth-static-conf.json',
                'oxtrust.config.reload'
                ]

ldap_creds = ['-h',
              'localhost',
              '-p',
              '1389',
              '-D',
              '"cn=directory',
              'manager"',
              '-j',
              password_file
              ]

fixManagerGroupLdif = """dn: %s
changetype: modify
replace: gluuManagerGroup
gluuManagerGroup: %s

"""

bulk_ldif_files =  [ "groups.ldif",
                "people.ldif",
                "u2f.ldif",
                "attributes.ldif",
                "hosts.ldif",
                "scopes.ldif",
                "uma.ldif",
                "clients.ldif",
                "scripts.ldif",
                "site.ldif"
                ]

class MyLDIF(LDIFParser):
    def __init__(self, input, output):
        LDIFParser.__init__(self, input)
        self.targetDN = None
        self.targetAttr = None
        self.DNs = []
        self.lastDN = None
        self.lastEntry = None

    def getResults(self):
        return (self.targetDN, self.targetAttr)

    def getDNs(self):
        return self.DNs

    def getLastEntry(self):
        return self.lastEntry

    def handle(self, dn, entry):
        self.lastDN = dn
        self.DNs.append(dn)
        self.lastEntry = entry
        if dn.lower().strip() == targetDN.lower().strip():
            if entry.has_key(self.targetAttr):
                self.targetAttr = entry(self.targetAttr)

def backupEntry(dn, fn):
    # Backup the appliance config just in case!
    args = [ldapsearch] + ldap_creds + \
           ['-b',
           dn,
            '-s',
            'base',
           'objectclass=*']
    output = getOutput(args)
    f = open(fn, 'w')
    f.write(output)
    f.close()
    if output.find('dn') < 0:
        logIt("Error backing up entry %s" % dn, True)
        sys.exit(1)
    else:
        logIt("Wrote %s to %s" % (dn, fn))

def backupCurrentConfig():
    # Backup Appliance
    inum =  getAttributeValue(appliance_config_old_fn, 'inum')[0]
    backupEntry("inum=%s,ou=appliances,o=gluu" % inum, appliance_config_new_fn)
    # Backup Organization
    orgInum =  getAttributeValue(organization_config_old_fn, 'o')[0]
    backupEntry("o=%s,o=gluu" % orgInum, organization_config_new_fn)
    # Backup oxAuth Config
    dn = 'ou=oxauth,ou=configuration,inum=%s,ou=appliances,o=gluu' % inum
    backupEntry(dn, oxAuth_config_new_fn)
    # Backup oxTrust Config
    dn = 'ou=oxtrust,ou=configuration,inum=%s,ou=appliances,o=gluu' % inum
    backupEntry(dn, oxTrust_config_new_fn)

def copyFiles():
    os.path.walk ("./etc", walk_function, None)
    os.path.walk ("./opt", walk_function, None)

def getAttributeValue(fn, targetAttr):
    # Load oxAuth Config From LDIF
    parser = MyLDIF(open(fn, 'rb'), sys.stdout)
    parser.targetAttr = targetAttr
    parser.parse()
    value = parser.targetAttrValue
    return value

def getEntry(fn):
    parser = MyLDIF(open(fn, 'rb'), sys.stdout)
    parser.parse()
    return parser.lastDN, parser.lastEntry

def getOutput(args):
        try:
            logIt("Running command : %s" % " ".join(args))
            output = os.popen(" ".join(args)).read().strip()
            return output
        except:
            logIt("Error running command : %s" % " ".join(args), True)
            logIt(traceback.format_exc(), True)
            sys.exit(1)

def logIt(msg, errorLog=False):
    if errorLog:
        f = open(logError, 'a')
        f.write('%s %s\n' % (time.strftime('%X %x'), msg))
        f.close()
    f = open(log, 'a')
    f.write('%s %s\n' % (time.strftime('%X %x'), msg))
    f.close()

def restoreConfig(oldFn, newFn, fn_base):
    ignoreList = ['objectClass', 'ou']
    counter = 0
    old_dn, old_entry = getEntry(oldFn)
    new_dn, new_entry = getEntry(newFn)
    for attr in old_entry.keys():
        if attr in ignoreList:
            continue
        logIt("%s config update: updating %s" % (fn_base, attr))
        if new_entry.has_key(attr):
            mod_list = None
            if old_entry[attr] != new_entry[attr]:
                counter = counter + 1
                if len(new_entry[attr]) == 1:
                    old_json = json.loads(old_entry[attr][0])
                    new_json = json.loads(new_entry[attr][0])
                    new_json = merge(new_json, old_json)
                    mod_list = [json.dumps(new_json)]
                    logIt("Merging json value for %s " % attr)
                else:
                    mod_list = old_entry[attr]
                    logIt("Keeping multiple old values for %s" % attr)
            else:
                logIt("%s config update: no changes found for %s" % (fn_base, attr))
                continue
            writeMod(old_dn, attr, mod_list, '%s/%s%i.ldif' % (bu_folder, fn_base, counter))
        else:
            counter = counter + 1
            writeMod(old_dn, attr, old_entry[attr], '%s/%s%i.ldif' % (bu_folder, fn_base, counter), True)
            logIt("Adding attr %s which was not found in new ldif" % attr)

def startOpenDJ():
    output = getOutput([service, 'opendj', 'start'])
    if output.find("Directory Server has started successfully") > 0:
        logIt("Directory Server has started successfully")
    else:
        logIt("OpenDJ did not start properly... exiting. Check /opt/opendj/logs/errors")
        sys.exit(2)

def stopOpenDJ():
    output = getOutput([service, 'opendj', 'start'])
    if output.find("Directory Server is now stopped") > 0:
        logIt("Directory Server is now stopped")
    else:
        logIt("OpenDJ did not stop properly... exiting. Check /opt/opendj/logs/errors")
        sys.exit(3)

def tab_attr(attr, base64text, encoded=False):
    targetLength = 80
    lines = ['%s: ' % attr]
    if encoded:
        lines = ['%s:: ' % attr]
    for char in base64text:
        current_line = lines[-1]
        if len(current_line) < 80:
            new_line = current_line + char
            del lines[-1]
            lines.append(new_line)
        else:
            lines.append(" " + char)
    return "\n".join(lines)

def uploadLDIF():
    for ldif_file in ldif_files:
        cmd = [ldapmodify] + ldap_creds + ['-a', '-c', '-f', '%s/%s' % (ldif_folder, ldif_file)]
        output = getOutput(cmd)
        if output:
            logIt(output)

    # Load SAML Trust Relationships
    fn = "./ldif/trust_relationships.ldif"
    cmd = [ldapmodify] + ldap_creds + ['-a', '-f', fn]
    output = getOutput(cmd)
    if output:
        logIt(output)

    files = os.listdir(configLdifFolder)
    for fn in files:
        cmd = [ldapmodify] + ldap_creds + ['-a', '-f', "%s/%s" % (configLdifFolder, fn)]
        output = getOutput(cmd)
        if output:
            logIt(output)

def walk_function(a, dir, files):
    for file in files:
        if file in ignore_files:
            continue
        fn = "%s/%s" % (dir, file)
        targetFn = fn[1:]
        if os.path.isdir(fn):
            if not os.path.exists(targetFn):
                os.mkdir(targetFn)
        else:
            # It's a file...
            try:
                logIt("copying %s" % targetFn)
                shutil.copyfile(fn, targetFn)
            except:
                logIt("Error copying %s" % targetFn, True)

def writeMod(dn, attr, value_list, fn, add=False):
    operation = "replace"
    if add:
        operation = "add"
    modLdif = """dn: %s
changetype: modify
%s: %s\n""" % (dn, operation, attr)
    if value_list == None: return
    for val in value_list:
        val = str(val).strip()
        if val.find('\n') > -1:
            val = base64.b64encode(val)
            modLdif = modLdif + "%s\n" % tab_attr(attr, val, True)
        elif len(val) > (78 - len(attr)):
            modLdif = modLdif + "%s\n" % tab_attr(attr, val)
        else:
            modLdif = modLdif + "%s: %s\n" % (attr, val)
    f = open(fn, 'w')
    f.write(modLdif)
    f.close()

error = False
try:
    ldif_folder = sys.argv[1]
    if not os.path.exists(ldif_folder):
        error = True
except:
    error = True

if error:
    print "ldif folder not found"
    print "Usage: ./import.py path_to_ldif_folder"
    sys.exit(1)

if not os.path.exists(ouputFolder):
    os.mkdir(ouputFolder)

if not os.path.exists(configLdifFolder):
    os.mkdir(configLdifFolder)

stopOpenDJ()
copyFiles()
startOpenDJ()
restoreConfig(oxAuth_config_old_fn, oxAuth_config_new_fn, "oxauth")
restoreConfig(oxTrust_config_old_fn, oxAuth_config_new_fn, "oxtrust")
restoreConfig(organization_config_old_fn, organization_config_new_fn, "organization")
restoreConfig(appliance_config_old_fn, appliance_config_new_fn, "appliance")
uploadLDIF()
