# coding: utf-8
# Copyright 2013 The Font Bakery Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# See AUTHORS.txt for the list of Authors and LICENSE.txt for the License.
from __future__ import print_function

import codecs
import datetime
import os.path as op
import redis
import yaml

from flask.ext.rq import job

from checker import run_set, parse_test_results
from cli.system import os, prun
from cli.utils import RedisFd


@job
def refresh_latest_commits():
    from scripts.gitupdate import main
    main()


@job
def refresh_repositories(username, token):
    from bakery.app import github
    from bakery.github import GithubSessionAPI, GithubSessionException
    from bakery.settings.models import ProjectCache
    _github = GithubSessionAPI(github, token)
    try:
        repos = _github.get_repo_list()
        ProjectCache.refresh_repos(repos, username)
    except GithubSessionException, ex:
        print(ex.message)


def get_subsets_coverage_data(source_fonts_paths, log=None):
    """ Return dict mapping key to the corresponding subsets coverage.

    For example:

    {'latin': 86, 'devanagari': 72}
    """
    from fontaine.font import FontFactory
    from fontaine.cmap import Library
    library = Library(collections=['subsets'])
    subsets = {}
    for fontpath in source_fonts_paths:
        try:
            font = FontFactory.openfont(fontpath)
        except AssertionError, ex:
            if log:
                log.write('Error: [%s] %s' % (fontpath, ex.message))
            continue
        for charmap, _, coverage, _ in \
                font.get_orthographies(_library=library):
            subsets[charmap.common_name.replace('Subset ', '')] = coverage
    return subsets


def generate_subsets_coverage_list(project, log=None):
    """ Generates available subsets from project sources.

        Method writes result to yaml file to avoid calling pyfontaine
        api each time.

        Args:
            project: A :class:`~bakery.project.models.Project` instance
            log: A :class:`~bakery.utils.RedisFd` instance

        Returns:
            Sorted subsets from prepared yaml file in tuple
            [(common_name, coverage),]

    """
    from .app import app
    if log:
        log.write('PyFontaine subsets with coverage values\n')

    _in = joinroot('%(login)s/%(id)s.in/' % project)
    ufo_dirs, ttx_files, _ = get_sources_lists(_in)

    _out_yaml = op.join(app.config['DATA_ROOT'],
                        '%(login)s/%(id)s.out/fontaine.yml' % project)
    if op.exists(_out_yaml):
        return sorted(yaml.safe_load(open(_out_yaml, 'r')).items())

    if not op.exists(op.dirname(_out_yaml)):
        os.makedirs(op.dirname(_out_yaml), log=log)

    source_fonts_paths = []
    # `get_sources_list` returns list of paths relative to root.
    # To complete to absolute paths use python os.path.join method
    # on root and path
    for path in ufo_dirs + ttx_files:
        source_fonts_paths.append(op.join(_in, path))
    subsets = get_subsets_coverage_data(source_fonts_paths, log)

    contents = yaml.safe_dump(subsets)

    yamlf = codecs.open(_out_yaml, mode='w', encoding="utf-8")
    yamlf.write(contents)
    yamlf.close()

    return sorted(yaml.safe_load(open(_out_yaml, 'r')).items())


@job
def project_git_sync(project):
    """ Sync git repo, or download it if it doesn't yet exist.

    Args:
        project: A :class:`~bakery.models.Project` instance
        log: A :class:`~bakery.utils.RedisFd` instance
    """
    from bakery.app import db, app
    project.is_ready = False
    db.session.add(project)
    db.session.commit()
    db.session.refresh(project)

    client = redis.StrictRedis()

    _in = joinroot('%(login)s/%(id)s.in/' % project)
    _out = joinroot('%(login)s/%(id)s.out/' % project)
    if not op.exists(_out):
        os.makedirs(_out)

    try:
        os.remove(op.join(_out, 'fontaine.yml'))
    except OSError:
        pass

    try:
        os.remove(op.join(_out, 'upstream.log'))
    except OSError:
        pass

    log = RedisFd(op.join(_out, 'upstream.log'))
    # Create the incoming repo directory (_in) if it doesn't exist
    if not op.exists(_in):
        os.makedirs(op.join(app.config['DATA_ROOT'], _in), log=log)

    # Update _in if it already exists with a .git directory
    from git import Repo, InvalidGitRepositoryError
    try:
        repo = Repo(_in)
        log.write('$ git reset --hard\n')
        log.write(repo.git.reset(hard=True) + '\n')
        log.write('$ git clean --force\n')
        repo.git.clean(force=True)
        log.write('$ git pull origin master\n')
        repo.remotes.origin.pull()
    except InvalidGitRepositoryError:
        # clone the repository
        # log.write('Copying Git Repository\n', prefix='### ')
        try:
            # TODO in the future, to validate the URL string use
            # http://schacon.github.io/git/git-ls-remote.html
            # http://stackoverflow.com/questions/9610131/how-to-check-the-validity-of-a-remote-git-repository-url
            prun(('git clone --progress --depth=100'
                  ' --branch=master %(clone)s .') % project, cwd=_in, log=log)
        except:
            # if the clone action didn't work, just copy it
            # if this is a file URL, copy the files, and set up
            # the _in directory as a git repo
            if project.clone[:7] == "file://":
                # cp recursively, keeping all attributes, not following
                # symlinks, not deleting existing files, verbosely
                prun('cp -a %(clone)s .' % project, cwd=_in, log=log)
                #
                prun('git init .', cwd=_in, log=log)
                prun('git add *', cwd=_in, log=log)
                msg = "Initial commit made automatically by Font Bakery"
                prun('git commit -a -m "%s"' % msg, cwd=_in, log=log)
            else:
                raise
        # Now we have it, create an initial project state
        finally:
            config = project.config

    generate_subsets_coverage_list(project, log=log)

    revision = prun("git rev-parse --short HEAD", cwd=_in).strip()
    upstream_revision_tests(project, revision, log=log)

    log.write('End: Repository is ready. Please Setup\n', prefix='### ')
    # set project state as ready after sync is done
    project.is_ready = True
    project.latest_commit = revision

    import urlparse
    pp = urlparse.urlparse(project.clone)
    if pp.netloc == 'github.com':
        project.is_github = True
        full_name = os.path.splitext(pp.path.strip('/'))[0]
        project.full_name = full_name
        project.name = full_name.split('/')[1]

    project.last_updated = datetime.datetime.now()

    db.session.add(project)
    db.session.commit()

    import json
    client.publish('global:%s' % project.login,
                   json.dumps({'type': 'UPSTREAMFINISHED',
                               'project_id': project.id}))


def joinroot(path):
    from bakery.app import app
    return op.join(app.config['DATA_ROOT'], path)


class PathParam:

    def __init__(self, project, build):
        self.param = {'login': project.login, 'id': project.id,
                      'revision': build.revision, 'build': build.id}

        self._in = joinroot('%(login)s/%(id)s.in/' % self.param)
        self._out = joinroot('%(login)s/%(id)s.out/%(build)s.%(revision)s/' % self.param)

        path = '%(login)s/%(id)s.out/%(build)s.%(revision)s/sources/' % self.param
        self._out_src = joinroot(path)


def get_sources_lists(rootpath):
    """ Return list of lists of UFO, TTX and METADATA.json """
    ufo_dirs = []
    ttx_files = []
    metadata_files = []
    l = len(rootpath)
    for root, dirs, files in os.walk(rootpath):
        for f in files:
            fullpath = op.join(root, f)
            if op.splitext(fullpath[l:])[1].lower() in ['.ttx', ]:
                if fullpath[l:].count('.') > 1:
                    continue
                ttx_files.append(fullpath[l:])
            if f.lower() == 'metadata.json':
                metadata_files.append(fullpath[:l])
        for d in dirs:
            fullpath = op.join(root, d)
            if op.splitext(fullpath)[1].lower() == '.ufo':
                ufo_dirs.append(fullpath[l:])
    return ufo_dirs, ttx_files, metadata_files


def upstream_revision_tests(project, revision, log=None):
    """ This function run upstream tests on all sources fonts in project.

        This mean that success (aka getting any result) should be occasional
        particular case. Because data and set of folders are changing during
        font development process.

        Args:
            project: A :class:`~bakery.models.Project` instance
            revision: Git revision

        Returns:
            A dict with serialized tests results formatted by `repr_testcase`.
    """
    param = {'login': project.login, 'id': project.id, 'revision': revision}

    _in = joinroot('%(login)s/%(id)s.in/' % project)
    _out_folder = joinroot('%(login)s/%(id)s.out/utests/' % param)
    _out_yaml = op.join(_out_folder, '%(revision)s.yaml' % param)

    if op.exists(_out_yaml):
        return yaml.safe_load(open(_out_yaml, 'r'))

    if not op.exists(_out_folder):
        os.makedirs(_out_folder)

    result = {}
    os.chdir(_in)

    try:
        git_checkout(_in, revision)

        result[project.clone] = run_set(_in, 'upstream-repo', log=log)

        ufo_dirs, ttx_files, metadata_files = get_sources_lists(_in)

        for font in ufo_dirs:
            if op.exists(op.join(_in, font)):
                result[font] = run_set(op.join(_in, font), 'upstream', log=log)

        for metadata_path in metadata_files:
            result[metadata_path] = run_set(metadata_path, 'metadata', log=log)

        for font in ttx_files:
            if op.exists(op.join(_in, font)):
                result[font] = run_set(op.join(_in, font), 'upstream-ttx', log=log)

        l = open(_out_yaml, mode='w')
        l.write(yaml.safe_dump(result))
        l.close()

        return yaml.safe_load(open(_out_yaml, 'r'))
    except Exception, ex:
        if log:
            log.write('UPSTREAM: FAILED')
            log.write('Details: %s' % ex)
        return {}


def git_checkout(path, revision, log=None):
    from git import Repo
    repo = Repo(path)
    if log:
        headcommit = repo.git.rev_parse('HEAD', short=True)
        if headcommit != revision:
            log.write('Checkout commit %s\n' % revision, prefix='### ')
        else:
            log.write('Checkout most recent commit (%s)' % revision,
                      prefix='### ')
        log.write("git checkout %s\n" % revision, prefix='$ ')
    repo.git.checkout(revision)


class DBTaskSet(object):

    def __init__(self, project_id, build_id, revision):
        self.project_id = project_id
        self.build_id = build_id
        self.revision = revision

    def create_task(self, message):
        from bakery.app import db
        from bakery.project.models import Task
        task = Task()
        task.description = message
        task.build_id = self.build_id
        task.revision = self.revision
        task.project_id = self.project_id
        db.session.add(task)
        db.session.commit()
        return task

    def close_task(self, task, failed=False):
        from bakery.app import db
        task.done = True
        task.failed = failed
        db.session.commit()


@job
def process_project(project, build, force_sync=False):
    """ Runs bake the project.

    Args:
        project: :class:`~bakery.models.Project` instance
        build: :class:`~bakery.models.ProjectBuild` instance
        force_sync: means that project has to be checked out before baking
    """
    from bakery.app import app
    from cli.bakery import Bakery

    if force_sync:
        project_git_sync(project)

    param = {'login': project.login, 'id': project.id,
             'revision': build.revision, 'build': build.id}

    project_dir = '%(id)s.in' % param
    builds_dir = '%(id)s.out' % param
    build_dir = '%(build)s.%(revision)s' % param

    # setup is set after 'bake' button is first pressed
    if project.config['local'].get('setup'):

        # this code change upstream repository
        root = joinroot('%(login)s' % param)
        b = Bakery(root, project_dir, builds_dir, build_dir)

        config = os.path.join(root, project_dir, '.bakery.yaml')
        b.load_config(config)

        logfile = '%(build)s.%(revision)s.process.log' % param
        b.init_logging(logfile)

        b.init_taskset(DBTaskSet(project.id, build.id, build.revision))

        try:
            config = b.run()

            if 'zip' in config:
                # zip out folder with revision
                url = app.config['DATA_URL']
                url += '%(login)s/%(id)s.out' % param
                _ = 'Link to archive [%(name)s.zip](%(url)s/%(name)s)\n'
                _ = _ % {'name': config['zip'], 'url': url}
                b.log.write(_, prefix="### ")

            b.log.write(('Bake Succeeded! Now see '
                         '[Build History](/project/%s/build)\n') % project.id,
                        prefix='### ')
        except Exception:
            build.failed = True
            raise
        finally:
            # save that project is done
            set_done(build)


@job
def process_description_404(project, build):
    """ Background task to check links in DESCRIPTION.en_us.html file

    This method generates yaml file `*.*.404links.yaml` inside repo out
    directory.

    Args:
        project: A :class:`~bakery.models.Project` instance
        build: A :class:`~bakery.models.ProjectBuild` instance
    """
    path_params = PathParam(project, build)
    path = op.join(path_params._out, 'DESCRIPTION.en_us.html')

    param = {'login': project.login, 'id': project.id,
             'revision': build.revision, 'build': build.id}
    _out_yaml = joinroot(('%(login)s/%(id)s.out/'
                          '%(build)s.%(revision)s.404links.yaml') % param)

    result = {}
    test_results = run_set(path, 'description')
    result = parse_test_results(test_results)
    result['updated'] = datetime.datetime.now()

    # Comment during debug
    l = open(_out_yaml, 'w')
    l.write(yaml.safe_dump(result))
    l.close()

    d = yaml.safe_load(open(_out_yaml, 'r'))
    # os.remove(_out_yaml)
    return d


def set_done(build):
    """ Set done flag for build """
    from bakery.app import db
    build.is_done = True
    db.session.add(build)
    db.session.commit()


@job
def execute_metadata_tests(login, project_id, build_id, revision):
    param = {'login': login, 'id': project_id,
             'revision': revision, 'build': build_id}
    builddir = joinroot('%(login)s/%(id)s.out/%(build)s.%(revision)s/' % param)

    from cli.pipe.fontlint import FontLint
    fontlint = FontLint('', builddir)
    testsresult = fontlint.read_lint_testsresult()
    testsresult['METADATA.json'] = fontlint.run_metadata_tests()
    fontlint.write_lint_results(testsresult)
