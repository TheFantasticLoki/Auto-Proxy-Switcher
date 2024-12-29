# Automatic Proxy Switcher

## Overview:

This is a somewhat simple script you can run on your local computer that automatically scrapes free proxy lists, tests them, and then selects the best one and routes your traffic to that proxy. This should also automatically fallback to another proxy in the working list if the one in use starts to fail. You connect to this script like you would any other proxy, such as using a PAC script with an extension of your choise with the proxy address being localhost:8080. 



## Features:

- Scraping of free proxy lists

- Testing of scraped proxies

- Traffic routing to best working proxy

- Dual panel console UI with command input interface. (Left = Normal, Right = Proxy Tests)



## Reason for creation:

I won't lie to you, this was made specifically to get around blocks on sites like the Hub in states where BS legislation was passed forcing users to provide state ID's to websites for age verification. Every website out there knows this is BS and completely unsafe and have decided to just not serve users in these states. You can get around these blocks by utilizing a proxy but many either can't afford dedicated proxies/VPNs or just can't be bothered but also don't want to spend 4 hours manually finding and testing hundreds of proxies from free proxy lists just to find 1 working proxy and then be forced to do the whole process over again in a few weeks. This script solves this problem to the best of my ability by doing the whole process automatically. Just launch the script, connect to it, and enjoy.
