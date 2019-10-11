#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
from .console import EndlessConsole, Console
import logging
import time
from . import config
from github import Github
import urllib3

class Distrib():
    """
    Cleep distribution helper
    """

    GITHUB_USER = 'tangb'
    GITHUB_REPO = 'cleep'

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.__endless_command_running = False
        self.__endless_command_return_code = 0
        self.http = urllib3.PoolManager(num_pools=1)

    def __console_callback(self, stdout, stderr):
        self.logger.info((stdout if stdout is not None else '') + (stderr if stderr is not None else ''))

    def __console_end_callback(self, return_code, killed):
        self.__endless_command_running = False
        self.__endless_comand_return_code = return_code

    def build_cleep(self):
        """
        Build cleep debian package
        """
        self.logger.info('Building Cleep package...')
        cmd = """
SENTRY_DSN=`printenv SENTRY_DSN`

if [ -z "$SENTRY_DSN" ]; then
    echo 
    echo "ERROR: sentry DSN not defined, please set an environment variable called SENTRY_DSN with valid data"
    echo
    exit 1
fi

#clean all files
clean() {
    echo `pwd`
    rm -rf build
    rm -rf debian/raspiot
    rm -rf debian/*debhelper*
    rm -rf ../raspiot_*_armhf.*
    rm -rf tmp
}

#jump in cleep root directory
cd "%s"

#clean previous build
clean

#check python version
VERSION=`head -n 1 debian/changelog | awk '{ gsub("[\(\)]","",$2); print $2 }'`
PYTHON_VERSION=`cat raspiot/__init__.py | grep $VERSION | wc -l`
if [ "$PYTHON_VERSION" -ne "1" ]
then
    echo
    echo "ERROR: python version is not the same than debian version, please update raspiot/__init__.py __version__ to $VERSION"
    echo
    exit 1
fi

#generate /etc/default/raspiot.conf
mkdir tmp
touch tmp/raspiot.conf
echo "SENTRY_DSN=$SENTRY_DSN" >> tmp/raspiot.conf

#build raspiot application
debuild -us -uc

#clean python stuff
rm -rf raspiot.egg-info
rm -rf pyraspiot.egg-info/
rm -rf tmp/

#jump in build output
cd ".."

#collect variables
DEB=`ls -A1 raspiot* | grep \.deb`
ARCHIVE=raspiot_$VERSION.zip
SHA256=raspiot_$VERSION.sha256
PREINST=cleep/scripts/preinst.sh
POSTINST=cleep/scripts/postinst.sh

#build zip archive
rm -f *.zip
rm -f *.sha256
cp -a $DEB raspiot.deb
cp -a $PREINST .
cp -a $POSTINST .
zip $ARCHIVE raspiot.deb `basename $PREINST` `basename $POSTINST`
rm -f `basename $PREINST`
rm -f `basename $POSTINST`
rm -f raspiot.deb
sha256sum $ARCHIVE > $SHA256
        """ % (config.REPO_DIR)
        self.__endless_command_running = True
        c = EndlessConsole(cmd, self.__console_callback, self.__console_end_callback)
        c.start()

        while self.__endless_command_running:
            time.sleep(0.25)

        self.logger.debug('Return code: %s' % self.__endless_command_return_code)
        if self.__endless_command_return_code!=0:
            return False

        return True

    def __delete_github_tag(self, tag_name):
        """
        Delete specified tag name (this feature is not available on PyGithub API)
        """
        #curl request
        #curl -X DELETE -H "Authorization: token <token>" -H "User-Agent: PyGithub/Python" https://api.github.com/repos/tangb/cleep/git/refs/tags/<tag_name>
        try:
            resp = self.http.request('DELETE', 'https://api.github.com/repos/%s/%s/git/refs/tags/%s' % (self.GITHUB_USER, self.GITHUB_REPO, tag_name))
            if resp.status==200:
                return True
            else:
                self.logger.error('Unable to delete tag [status=%s]' % resp.status)
                return False
        except:
            self.logger.exception('Error deleting tag "%s"' % tag_name)
            return False

    def publish_cleep(self, version):
        """
        Publish cleep version on github
        """
        github = Github(os.environ['GITHUB_ACCESS_TOKEN'])
        repo = github.get_repo('%s/%s' % (self.GITHUB_USER, self.GITHUB_REPO))

        #check build existence
        archive = os.path.join(config.REPO_DIR, '..', 'raspiot_%s.zip' % version)
        sha256 = os.path.join(config.REPO_DIR, '..', 'raspiot_%s.sha256' % version)
        changes = os.path.join(config.REPO_DIR, '..', 'raspiot_%s_armhf.changes' % version)
        if not os.path.exists(archive):
            self.logger.error('Archive file "%s" does not exist' % archive)
        if not os.path.exists(sha256):
            self.logger.error('Checksum file "%s" does not exist' % sha256)
        if not os.path.exists(changes):
            self.logger.error('Changes file "%s" does not exist' % changes)

        #get changelog
        cmd = 'sed -n "/raspiot (%(version)s)/,/Checksums-Sha1:/{/raspiot (%(version)s)/b;/Checksums-Sha1:/b;p}" %(changes)s | tail -n +2' % {'version': version, 'changes': changes}
        self.logger.debug('Cmd = %s' % cmd)
        c = Console()
        result = c.command(cmd)
        if result['error']:
            self.logger.error('Unable to read changelog')
        changelog = '\n'.join([line.strip() for line in result['stdout']])
        self.logger.debug('Changelog:\n%s' % changelog)

        #search existing release
        release_found = None
        releases = repo.get_releases()
        for release in releases:
            self.logger.debug('%s' % release.title)
            if release.title==version:
                self.logger.debug(' -> Release found')
                release_found = release
                break

        if release_found and (release_found.prerelease or release_found.draft):
            #due to github limitation (bug or limitation?), draft assets are not downloadable
            #so we create prerelease version instead of draft and delete it before creating it
            #again when pushing version
            self.logger.info('Deleting existing version...')
            try:
                release_found.delete_release()
            except:
                self.logger.exception('Error deleting existing release:')
                return False

            #delete tag
            if not self.__delete_github_tag(release_found.tag_name):
                return False
        
        #create release
        self.logger.info('Creating new release "%s"...' % version)
        try:
            commits = repo.get_commits()
            release_found = repo.create_git_release(
                tag='v%s' % version,
                name=version,
                message=changelog,
                draft=False,
                prerelease=True,
            )
        except:
            self.logger.exception('Error occured creating new release:')
            return False

        #upload assets
        try:
            self.logger.info('Uploading asset "%s"...' % archive)
            release_found.upload_asset(archive)
            self.logger.info('Uploading asset "%s"...' % sha256)
            release_found.upload_asset(sha256)
        except:
            self.logger.exception('Error uploading assets:')
            return False

        return True

        #TODO code for draft release removed for problem downloading draft assets
        #if not release_found:
        #    #create release
        #    self.logger.info('Creating new release "%s"...' % version)
        #    try:
        #        commits = repo.get_commits()
        #        release_found = repo.create_git_release(
        #            tag='v%s' % version,
        #            name=version,
        #            message=changelog,
        #            draft=True,
        #            prerelease=False,
        #        )
        #    except:
        #        self.logger.exception('Error occured creating new release:')
        #        return False

        #else:
        #    #only update draft release !
        #    if not release_found.draft:
        #        self.logger.error('Existing release "%s" is not draft. Please create new release' % version)

        #    #update release data
        #    try:
        #        self.logger.info('Updating existing release "%s"...' % version)
        #        release_found.update_release(
        #            name=version,
        #            message=changelog,
        #            draft=True,
        #            prerelease=False,
        #        )

        #        #delete all assets
        #        self.logger.info('Deleting all existing release assets...')
        #        for asset in release_found.get_assets():
        #            asset.delete_asset()
        #    except:
        #        self.logger.exception('Error occured creating new release:')
        #        return False


