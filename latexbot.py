import discord
import urllib.request
import random
import zlib
import os
import json
import shutil
import asyncio
import sys
import subprocess

import chanrestrict

QUERIES_SUBDIR = 'queries'
LATEX_TEMPLATE = 'template.tex'
USER_AGENT = ' '.join(['Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36',
	'(KTHML, like Gecko) Chrome/57.0.2987.133 Safari/537.36'])
LOG_MAX_LENGTH = 300 #maximum accepted by discord is 2000
BOT_OWNER = 'Saoirse'
SOURCE_CODE = 'https://github.com/diethnis/LatexBot'


HELP_MESSAGE = ''.join([
'I am the *LaTeX* math bot, originally from {}. '.format(SOURCE_CODE),
'I am running on one of {}\'s computers right now, '.format(BOT_OWNER),
'so once they leave or turn off their computer, I\'m gone, too.\n\n',
'To use me, type in !tex before your expression. Alternatively, '
'you can begin in an align* environment by using !eqn ',
'instead of !tex, which enters math mode automatically.\n\n',
'Note that the use of _, *, and \\ will appear to conflict with ',
'the Markdown syntax built into discord, though the bot will parse it right, ',
'regardless. This is because it does not read the formatted text but rather ',
'the *unformatted* text. For instance, a newline in LaTeX (two backslashes) ',
'will appear as only one backslash in chat because of Markdown, but will ',
'still typeset correctly.\n\n',
'If you get a syntax error, the right way to copy paste your previous code ',
'to edit it is to click "edit message" and copy from there, rather than ',
'the formatted output Discord gives you (which removes some backslashes).\n',
r"""
**Examples**

```
!tex $$x = 7$$

!tex \[ \sqrt{a^2 + b^2} = c \]

!tex Let's integrate $\int_0^{2\pi} \sin{(4\theta)} \mathrm{d}\theta$.

!eqn \lim_{n \to \infty} \frac{sin(n)}{n} = 0
```
"""
])

class LatexBot(discord.Client):
	#TODO: Check for bad token or login credentials using try catch
	def __init__(self):
		super().__init__()

		self.check_for_config()
		self.settings = json.loads(open('settings.json').read())

		chanrestrict.setup(self.settings['channels']['whitelist'],
							self.settings['channels']['blacklist'])

		if self.settings['login_method'] == 'token':
			self.run(self.settings['login']['token'])
		elif self.settings['login_method'] == 'account':
			self.run(self.settings['login']['email'],self.settings['login']['password'])
		else:
			raise Exception('Bad config: "login_method" should be either "account" or "token"')

		if not os.path.exists(QUERIES_SUBDIR):
			os.makedirs(QUERIES_SUBDIR)
			# possible bug: doesn't create directory

	def check_for_config(self):
		if not os.path.isfile('settings.json'):
			shutil.copyfile('settings_default.json', 'settings.json')
			print('Now you can go and edit "settings.json".')
			print('See README.md for more information on these settings.')
			sys.exit('No AUTH first run')

	def vprint(self, *args, **kwargs):
		if self.settings.get('verbose', False):
			print(*args, **kwargs)

	def datafromurl(self, *args, **kwargs):
		try:
			headers = {}
			headers['User-Agent'] = USER_AGENT
			req = urllib.request.Request(self, headers = headers)
			data = urllib.request.urlopen(req).read().decode('utf-8').replace('\r','')
			return(data)
		except Exception as e:
			print(str(e))

	# Outputs bot info to user
	@asyncio.coroutine
	def on_ready(self):
		print('Logged in to Discord as {}, with ID: {}'.format(self.user.name,self.user.id))
		print('-'*40)

	async def on_message(self, message):
		if chanrestrict.check(message):

			msg = message.content

			# TODO: rework command/plugin forwarding
			for c in self.settings['commands']['remote']:
				if msg.startswith(c):
					latex = LatexBot.datafromurl(msg[len(c):].strip())
					await self.handle_latex(message.channel, latex, is_eqn=False)
					return

			for c in self.settings['commands']['render']:
				if msg.startswith(c):
					latex = msg[len(c):].strip()
					await self.handle_latex(message.channel, latex, is_eqn=False)
					return

			for c in self.settings['commands']['equation']:
				if msg.startswith(c):
					latex = msg[len(c):].strip()
					await self.handle_latex(message.channel, latex, is_eqn=True)
					return

			if msg in self.settings['commands']['help']:
				self.vprint('Showing help')
				await self.send_message(message.channel, HELP_MESSAGE)

	async def handle_latex(self, channel, latex, is_eqn):
		#num = str(random.randint(0, 2 ** 31))
		# CRC to "cache" already generated images. chance of collision?
		# consider using hashlib.pbkdf2_hmac instead, or sha1 at least
		num = str(zlib.crc32(latex.encode('utf-8')))
		self.vprint('Latex query %s: %s' % (num, latex))
		if is_eqn:
			latex = "$\\displaystyle\n" + latex + "\n$"

		if self.settings['renderer'] == 'external':
			fn = self.generate_image_online(latex)
		if self.settings['renderer'] == 'local':
			try:
				fn = self.generate_image(latex, num)
			except subprocess.CalledProcessError as e:
				decoded = e.output.decode("utf-8").replace('\r', '').replace('\\n', '\n')
				self.vprint('Latex error for file %s:\n%s' % (num, decoded))
				decoded = decoded.split('\n', 1)[-1].split('\n')
				decoded = '\n'.join([x for x in decoded if not num in x])
				if len(decoded)>LOG_MAX_LENGTH:
					await self.send_message(channel,
						'Error, long log: {}'.format(self.paste_logs(decoded)))
				else:
					await self.send_message(channel, '```Error:\n%s```' % decoded)
				return
			except Exception as e:
				await self.send_message(channel,
					'Error! Sadly, I can\'t tell you exactly what went wrong...')
				print('Unexpected exception!\n%s' % e)
				return

		if fn and os.path.getsize(fn) > 0:
			await self.send_file(channel, fn)
			self.vprint('Success.')
		else:
			await self.send_message(channel,
				'Error! Sadly, I can\'t tell you exactly what went wrong...')
			self.vprint('Failure.')

	# Run XeLaTeX locally. TODO: check security for \write18/etc
	def generate_image(self, latex, name):
		latex_filename = name + '.tex'
		latex_file = os.path.join(QUERIES_SUBDIR, latex_filename)
		pdf_file = os.path.join(QUERIES_SUBDIR, name + '.pdf')
		png_file = os.path.join(QUERIES_SUBDIR, name + '.png')

		if os.path.isfile(pdf_file) and os.path.isfile(png_file):
			return png_file

		with open(LATEX_TEMPLATE, 'r') as textemplatefile:
			textemplate = textemplatefile.read()

			with open(latex_file, 'w') as tex:
				tex.write(textemplate.replace('__DATA__', latex))
				tex.flush()
				os.fsync(tex)

		texfot = ' '.join(['texfot --ignore Warning --ignore "Output written"',
			'--ignore "This is XeTeX" --ignore "No pages" --no-stderr'])
		result = subprocess.check_output(
				'cd %s && %s xelatex -interaction=nonstopmode %s' % (
				QUERIES_SUBDIR, texfot, latex_filename), shell=True)
		os.system('pdfcrop --margins "5 0 5 0" %s %s' % (pdf_file, pdf_file))
		os.system('convert -density 300 %s -quality 100 -background white -alpha remove %s' % (pdf_file, png_file))
		return png_file

	# More unpredictable, but probably safer for my computer.
	def generate_image_online(self, latex):
		url = 'http://frog.isima.fr/cgi-bin/bruno/tex2png--10.cgi?'
		url += urllib.parse.quote(latex, safe='')
		fn = str(random.randint(0, 2 ** 31)) + '.png'
		urllib.request.urlretrieve(url, fn)
		return fn

	def paste_logs(self, logdata):
		url = 'https://hastebin.com/documents'
		headers = {}
		headers['User-Agent'] = USER_AGENT
		req = urllib.request.Request(url,logdata.encode('utf-8'),headers=headers)
		key = json.load(urllib.request.urlopen(req))['key']
		link = 'https://hastebin.com/raw/{}'.format(key)
		return link


if __name__ == "__main__":
	LatexBot()
