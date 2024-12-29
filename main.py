import requests
import json
import os
import sys
import time
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from aiohttp_socks import ProxyConnectionError, ProxyConnector
import threading
import logging
import socket
import socks
import select
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
import ssl
import errno
import curses
from curses import wrapper
from curses.textpad import Textbox, rectangle

if os.name == 'nt':
    asyncio.set_event_loop(asyncio.SelectorEventLoop())
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

global failed_proxies

PROXY_LIST_FILE = "list.json"
TIMEDOUT_PROXIES_FILE = "timedout.json"
FAILED_PROXIES_FILE = "failed.json"
API_URL = "https://api.proxifly.dev/get-proxy"
PROXY_FETCH_INTERVAL = 60 * 60  # Fetch every 60 minutes
PROXY_TEST_INTERVAL = 60 * 3    # Test every 3 minutes
DEFAULT_TEST_URL = "http://httpbin.org/ip"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
    )

# Settings
class Settings:
    def __init__(self):
        self.dev = 1 # Toggle for Debug Logging
        self.fetching = 1  # Toggle for fetching new proxies (1 = enable, 0 = disable)
        self.testing = 1 # Toggle for testing of proxies (1 = enable, 0 = disable)
        self.moving_proxies = 1  # Toggle for moving proxies between lists (1 = enable, 0 = disable)
        self.retest = 1 # Toggle for retesting failed proxies

Config = Settings()

class CursesHandler(logging.Handler):
    def __init__(self, ui):
        super().__init__()
        self.ui = ui

    def emit(self, record):
        msg = self.format(record)
        # Use custom attribute 'panel' to determine output destination
        panel = getattr(record, 'panel', 'output')  # Default to output panel if not specified
        if panel == 'test':
            self.ui.write_test_result(msg)
        else:
            self.ui.write_output(msg)

def setup_logging(ui):
    root_logger = logging.getLogger()
    handler = CursesHandler(ui)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


class ConsoleUI:
    def __init__(self):
        self.screen = None
        self.output_win = None
        self.test_win = None
        self.input_win = None
        self.command_box = None
        
    def setup_windows(self):
        # Initialize color pairs
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        
        # Get screen dimensions
        height, width = self.screen.getmaxyx()
        
        # Create main output window (left side)
        self.output_win = curses.newwin(height-3, width//2, 0, 0)
        self.output_win.scrollok(True)
        
        # Create test results window (right side)
        self.test_win = curses.newwin(height-3, width//2, 0, width//2)
        self.test_win.scrollok(True)
        
        # Create command input window (bottom)
        self.input_win = curses.newwin(3, width, height-3, 0)
        self.input_win.box()
        
        # Create input textbox
        self.command_box = Textbox(self.input_win.derwin(1, width-4, 1, 2))
        
        # Initial refresh
        self.screen.refresh()
        self.output_win.refresh()
        self.test_win.refresh()
        self.input_win.refresh()

    def write_output(self, text):
        self.output_win.addstr(text + "\n")
        self.output_win.refresh()

    def write_test_result(self, text):
        self.test_win.addstr(text + "\n")
        self.test_win.refresh()

def start_ui(proxy_manager):
    def main(stdscr):
        ui = ConsoleUI()
        ui.screen = stdscr
        ui.setup_windows()
        setup_logging(ui)
        
        while True:
            # Clear the input window and redraw box
            ui.input_win.clear()
            ui.input_win.box()
            
            # Add prompt text after the box
            prompt = "Enter command: "
            ui.input_win.addstr(1, 2, prompt)
            ui.input_win.refresh()
            
            # Create textbox with correct positioning after prompt
            edit_win = ui.input_win.derwin(1, ui.input_win.getmaxyx()[1]-len(prompt)-4, 1, len(prompt)+2)
            ui.command_box = Textbox(edit_win)
            
            # Get command input
            command = ui.command_box.edit().strip()
            logging.info(f"Entered Command: {command}", extra={'panel': 'output'})
            
            if command == "exit":
                os._exit(0)
            
            # Handle other commands...
            
            # Process command here
            if command == "list":
                logging.info("[*] Working proxies:")
                working_proxies = [p for p in proxy_manager.proxies if isinstance(p.get('latency'), (float, int)) and p['latency'] < float('inf')]
                for proxy in working_proxies:
                    logging.info(f" - {proxy['proxy']} (Latency: {proxy.get('latency', 'Unknown')})")
            elif command == "next":
                working_proxies = [p for p in proxy_manager.proxies if isinstance(p.get('latency'), (float, int)) and p['latency'] < float('inf')]
                if working_proxies:
                    current_proxy_index = (current_proxy_index + 1) % len(working_proxies)
                    next_proxy = working_proxies[current_proxy_index]
                    logging.info(f"[*] Switching to proxy: {next_proxy['proxy']} (Latency: {next_proxy['latency']:.3f}s)")
                    # Set this as the preferred proxy
                    proxy_manager.current_proxy = next_proxy
                else:
                    logging.info("[!] No working proxies available")
            elif command == "current":
                if hasattr(proxy_manager, 'current_proxy'):
                    logging.info(f"[*] Current proxy: {proxy_manager.current_proxy['proxy']} (Latency: {proxy_manager.current_proxy['latency']:.3f}s)")
                else:
                    logging.info("[!] No proxy currently selected")
            elif command == "failed":
                logging.info("\n[*] Failed proxies:")
                for proxy in proxy_manager.failed_proxies:
                    logging.info(f" - {proxy['proxy']}")
            elif command == "timedout":
                logging.info("\n[*] Timed-out proxies:")
                for proxy in proxy_manager.timedout_proxies:
                    logging.info(f" - {proxy['proxy']}")
            elif command == "exit":
                logging.info("Exiting command interface...")
                break
            else:
                logging.info("[!] Unknown command. Try 'list', 'next', 'current', 'failed', 'timedout', or 'exit'.")

    wrapper(main)




# ProxyManager class to handle fetching, loading, testing, and saving proxies
class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.timedout_proxies = []
        self.failed_proxies = []
        self.semaphore = asyncio.Semaphore(10)
    
    def load_proxies(self):
        if os.path.exists(PROXY_LIST_FILE):
            with open(PROXY_LIST_FILE, 'r') as file:
                try:
                    self.proxies = json.load(file)
                except Exception as e:
                    logging.error('Failed to load json')
                    pass
        else:
            self.proxies = []

    def load_timedout_proxies(self):
        if os.path.exists(TIMEDOUT_PROXIES_FILE):
            with open(TIMEDOUT_PROXIES_FILE, 'r') as file:
                try:
                    self.timedout_proxies = json.load(file)
                except Exception as e:
                    logging.error('Failed to load json')
                    pass

    def load_failed_proxies(self):
        if os.path.exists(FAILED_PROXIES_FILE):
            with open(FAILED_PROXIES_FILE, 'r') as file:
                try:
                    self.failed_proxies = json.load(file)
                except Exception as e:
                    logging.error('Failed to load json')
                    pass

    def save_proxies(self):
        with open(PROXY_LIST_FILE, 'w') as file:
            for proxy in self.proxies:
                if proxy.get("latency") == float('inf'):
                    proxy["latency"] = "Infinity"
            json.dump(self.proxies, file, indent=4)

    def save_timedout_proxies(self):
        with open(TIMEDOUT_PROXIES_FILE, 'w') as file:
            json.dump(self.timedout_proxies, file, indent=4)

    def save_failed_proxies(self):
        with open(FAILED_PROXIES_FILE, 'w') as file:
            json.dump(self.failed_proxies, file, indent=4)

    def remove_duplicates_from_all_lists(self):
        """Clean all proxy lists of duplicates"""
        seen_proxies = set()
        unique_proxies = []
        unique_failed = []
        unique_timedout = []
        
        # Process working proxies
        for proxy in self.proxies:
            proxy_id = f"{proxy['ip']}:{proxy['port']}"
            if proxy_id not in seen_proxies:
                seen_proxies.add(proxy_id)
                unique_proxies.append(proxy)
                
        # Process failed proxies
        for proxy in self.failed_proxies:
            proxy_id = f"{proxy['ip']}:{proxy['port']}"
            if proxy_id not in seen_proxies:
                seen_proxies.add(proxy_id)
                unique_failed.append(proxy)
                
        # Process timedout proxies
        for proxy in self.timedout_proxies:
            proxy_id = f"{proxy['ip']}:{proxy['port']}"
            if proxy_id not in seen_proxies:
                seen_proxies.add(proxy_id)
                unique_timedout.append(proxy)
        
        self.proxies = unique_proxies
        self.failed_proxies = unique_failed
        self.timedout_proxies = unique_timedout
        
        # Save the cleaned lists
        self.save_proxies()
        self.save_failed_proxies()
        self.save_timedout_proxies()

    def is_proxy_duplicate(self, proxy):
        """Enhanced duplicate check across all lists"""
        proxy_id = f"{proxy['ip']}:{proxy['port']}"
        
        # Check all lists at once using sets
        all_proxies = set()
        
        # Add existing proxies to set
        all_proxies.update(f"{p['ip']}:{p['port']}" for p in self.proxies)
        all_proxies.update(f"{p['ip']}:{p['port']}" for p in self.failed_proxies)
        all_proxies.update(f"{p['ip']}:{p['port']}" for p in self.timedout_proxies)
        
        return proxy_id in all_proxies

    def fetch_proxies_from_apis(self):
        if Config.fetching:
            logging.info("[*] Fetching proxies from APIs...")
            
            # Fetch from both APIs
            new_proxies = self.fetch_from_proxifly()
            new_proxies += self.fetch_from_proxyscrape()
            
            added_count = 0
            for proxy in new_proxies:
                if not self.is_proxy_duplicate(proxy):
                    logging.info(f"[*] Adding new proxy: {proxy['proxy']}")
                    self.proxies.append(proxy)
                    added_count += 1
            
            logging.info(f"[*] Added {added_count} new unique proxies")
            
            # Clean all lists of any potential duplicates
            self.remove_duplicates_from_all_lists()
            
            # Save the updated lists
            self.save_proxies()

    def fetch_from_proxifly(self):
        params = {
            "format": "json",
            "protocol": ["http", "socks4"],
            "quantity": 1  # Adjust the quantity if needed
        }
        try:
            response = requests.post(API_URL, json=params, headers={'Content-Type': 'application/json'})
            if response.status_code == 200:
                new_proxies = response.json()
                return [new_proxies] if isinstance(new_proxies, dict) else new_proxies
            else:
                logging.info(f"[!] Failed to fetch proxies from Proxifly\n  [-] ({response.status_code}) {response.content}")
                return []
        except Exception as e:
            logging.info(f"[!] Exception during Proxifly fetch: {e}")
            return []

    def fetch_from_proxyscrape(self):
        try:
            response = requests.get("https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=us&proxy_format=protocolipport&format=json&timeout=1500")
            if response.status_code == 200:
                data = response.json()
                formatted_proxies = []
                for proxy in data["proxies"]:
                    if proxy["alive"]:  # Only include alive proxies
                        formatted_proxies.append({
                            "proxy": proxy["proxy"],
                            "protocol": proxy["protocol"],
                            "ip": proxy["ip"],
                            "port": proxy["port"],
                            "https": proxy["ssl"],
                            "anonymity": proxy["anonymity"],
                            "score": 1,
                            "geolocation": {
                                "country": proxy["ip_data"]["country"],
                                "city": proxy["ip_data"]["city"],
                                "region": proxy["ip_data"]["regionName"]
                            }
                        })
                return formatted_proxies
            else:
                logging.info(f"[!] Failed to fetch proxies from ProxyScrape\n  [-] ({response.status_code}) {response.content}")
                return []
        except Exception as e:
            logging.info(f"[!] Exception during ProxyScrape fetch: {e}")
            return []

    async def test_proxy(self, proxy_info):
        proxy_url = proxy_info.get("proxy")
        protocol = proxy_info.get("protocol")
        headers = {'User-Agent': 'Mozilla/5.0'}

        try:
            start = time.time()

            # Test both HTTP and HTTPS CONNECT capabilities
            async with aiohttp.ClientSession() as session:
                # Test 1: Basic HTTP request
                async with session.get("http://httpbin.org/ip", 
                                     proxy=proxy_url, 
                                     headers=headers, 
                                     timeout=10) as response:
                    if response.status != 200:
                        return float('inf')

                # Test 2: HTTPS CONNECT tunnel
                async with session.get("https://httpbin.org/ip", 
                                     proxy=proxy_url, 
                                     headers=headers, 
                                     timeout=10) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        if 'origin' in response_json:
                            end = time.time()
                            return end - start

            return float('inf')

        except Exception as e:
            logging.info(f"[!] Error testing proxy {proxy_url}: {e}", extra={'panel': 'test'})
            return float('inf')  # Return infinity to signify failure

    async def test_proxies(self, retest=False, batch_size=150):
        if retest:
            if Config.dev > 0:
                logging.info("Commencing Retest of Failed Proxies:")
            test_proxies = self.failed_proxies + self.timedout_proxies
        else:
            test_proxies = self.proxies.copy()  # Create a copy to safely modify
    
        working_proxies = []
        failed_batch = []  # Track failed proxies
    
        # Process proxies in batches
        for i in range(0, len(test_proxies), batch_size):
            batch = test_proxies[i:i + batch_size]
            tasks = [self.test_proxy(proxy) for proxy in batch]
            latencies = await asyncio.gather(*tasks)
    
            if Config.testing > 0:
                for proxy, latency in zip(batch, latencies):
                    if latency and latency != float('inf'):
                        proxy['latency'] = latency
                        working_proxies.append(proxy)
                    else:
                        logging.info(f"[!] Proxy failed: {proxy['proxy']}", extra={'panel': 'test'})
                        failed_batch.append(proxy)
    
        # Update the lists
        if not retest:
            self.proxies = working_proxies
            self.failed_proxies.extend(failed_batch)
        else:
            self.proxies.extend(working_proxies)
            self.failed_proxies = failed_batch
    
        self.save_proxies()
        self.save_failed_proxies()


# ProxyHandler class for relaying traffic
class ProxyHandler(BaseHTTPRequestHandler):
    proxy_manager = None  # Class variable to store proxy manager

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def send_error(self, code, message=None, explain=None):
        """Send a custom error response with HTML formatting."""
        error_messages = {
            502: "Bad Gateway",
            504: "Gateway Timeout", 
            503: "Service Unavailable",
            404: "Not Found",
            403: "Forbidden"
        }
    
        title = error_messages.get(code, "Unknown Error")
        
        if not message:
            message = title
        
        html_template = (
            f"<html><head><title>{code} {title}</title></head>\n"
            f"<body>\n"
            f"<h1>{code} {title}</h1>\n"
            f"<p>{message}</p>\n"
            f"{f'<p>{explain}</p>' if explain else ''}\n"
            f"</body></html>\n"
        )
    
        response = (
            f"HTTP/1.1 {code} {title}\r\n"
            f"Content-Type: text/html\r\n"
            f"Content-Length: {len(html_template)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{html_template}"
        )
    
        self.connection.send(response.encode())
        logging.error(f"[ERROR] {code} {message} {explain if explain else ''}")

    def handle_one_request(self):
        logging.info(f"[REQUEST] {self.command if hasattr(self, 'command') else 'UNKNOWN'}")
        try:
            return super().handle_one_request()
        except Exception as e:
            logging.error(f"[ERROR] In handle_one_request: {e}")
            raise

    def handle(self):
        logging.info("[ENTRY] Handle called")
        try:
            super().handle()
        except Exception as e:
            logging.error(f"[ERROR] In handle: {e}")
            raise

    def setup(self):
        logging.info("[ENTRY] Setup called")
        super().setup()

    def do_HEAD(self):
        logging.info("[TEST] HEAD request received")
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()

    def do_CONNECT(self):
        if Config.dev:
            logging.info(f"[->] Incoming CONNECT request from {self.client_address}")
            logging.info(f"[->] Target path: {self.path}")
            logging.info(f"[->] Headers: {self.headers}")

        # Get list of working proxies sorted by latency
        working_proxies = sorted([p for p in self.proxy_manager.proxies 
                                if isinstance(p.get('latency'), (float, int)) 
                                and p['latency'] < float('inf')],
                                key=lambda x: x['latency'])

        if not working_proxies:
            # Send a more descriptive error response
            self.send_error(502, "Connection: Closed", "No working proxies available. Please try again later.")
            return
    
        for proxy in working_proxies[:3]:  # Try up to 3 best proxies
            try:
                logging.info(f"[->] Attempting proxy: {proxy['proxy']}")
                self.handle_tunnel(proxy)
                return  # If successful, return from method
            except Exception as e:
                logging.error(f"[!] Proxy {proxy['proxy']} failed: {str(e)}")
                continue  # Try next proxy
        
        # If all retries failed
        self.send_error(502, "Connection: Closed", "All proxy connection attempts failed. Please try again later.")

    def do_GET(self):
        if Config.dev:
            logging.info(f"[->] Incoming GET request from {self.client_address}")
            logging.info(f"[->] Target URL: {self.path}")
            logging.info(f"[->] Headers: {self.headers}")

        working_proxy = self.select_best_proxy()
        if working_proxy:
            logging.info(f"[->] Selected proxy: {working_proxy['proxy']}")
            self.relay_request(working_proxy)
        else:
            logging.error("[!] No working proxies available")
            self.send_error(502, "No working proxies available.")

    def select_best_proxy(self):
        """Select the best working proxy based on manual selection or latency."""
        if hasattr(self.proxy_manager, 'current_proxy'):
            return self.proxy_manager.current_proxy

        working_proxies = [p for p in self.proxy_manager.proxies if isinstance(p.get('latency'), (float, int)) and p['latency'] < float('inf')]
        if working_proxies:
            working_proxies.sort(key=lambda p: p['latency'])
            return working_proxies[0]
        return None

    def handle_tunnel(self, proxy):
        try:
            proxy_host = proxy['ip']
            proxy_port = int(proxy['port'])
            target_host, target_port = self.path.split(':')
            target_port = int(target_port)

            if Config.dev:
                logging.info(f"[->] Establishing tunnel to {target_host}:{target_port} via {proxy_host}:{proxy_port}")

            # Try DNS resolution first
            try:
                socket.gethostbyname(target_host)
            except socket.gaierror:
                # Let the error response fully transmit
                self.send_error(502, "DNS Resolution Failed", "Unable to resolve hostname")
                return False

            # Create connection to proxy
            proxy_socket = socket.create_connection((proxy_host, proxy_port), timeout=10)

            # Send CONNECT request to proxy
            connect_req = f"CONNECT {self.path} HTTP/1.1\r\nHost: {self.path}\r\n\r\n"
            proxy_socket.sendall(connect_req.encode())

            # Read proxy response
            response = proxy_socket.recv(4096).decode()
            if Config.dev:
                logging.info(f"[<-] Proxy response: {response}")

            if "200" not in response.split('\n')[0]:
                self.send_error(502, "Proxy Connection Failed", response)
                return False

            # Send success response to client
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.wfile.flush()

            # Start bidirectional relay
            self.relay_data(self.connection, proxy_socket)
            return True

        except Exception as e:
            logging.error(f"[!] Tunnel error: {str(e)}")
            self.send_error(502, f"Tunnel connection failed", f"{str(e)}")
            return False
        finally:
            if 'proxy_socket' in locals():
                proxy_socket.close()

    def relay_request(self, proxy):
        """Relay HTTP GET requests via the selected proxy."""
        try:
            target_url = self.path
            proxy_url = f"http://{proxy['proxy']}"

            # Forward request to the proxy and relay response back to the client
            response = requests.get(target_url, proxies={"http": proxy_url, "https": proxy_url}, stream=True)
            self.send_response(response.status_code)

            # Forward headers
            for header, value in response.headers.items():
                self.send_header(header, value)
            self.end_headers()

            # Forward content
            for chunk in response.iter_content(chunk_size=4096):
                self.wfile.write(chunk)
        except Exception as e:
            logging.error(f"Error relaying request through proxy: {e}")
            self.send_error(502, "Bad gateway")

    def relay_data(self, client_socket, proxy_socket):
        """Relay data between client and proxy using select()."""
        try:
            # Set non-blocking mode
            client_socket.setblocking(False)
            proxy_socket.setblocking(False)

            # Initialize buffers
            client_buffer = b""
            proxy_buffer = b""

            while True:
                # Wait for data on either socket
                readable, writable, _ = select.select(
                    [client_socket, proxy_socket],
                    [client_socket, proxy_socket] if client_buffer or proxy_buffer else [],
                    [], 60)

                # Handle readable sockets
                for sock in readable:
                    if sock is client_socket:
                        try:
                            data = client_socket.recv(8192)
                            if not data:
                                return
                            proxy_buffer += data
                        except (socket.error, IOError) as e:
                            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                                raise

                    elif sock is proxy_socket:
                        try:
                            data = proxy_socket.recv(8192)
                            if not data:
                                return
                            client_buffer += data
                        except (socket.error, IOError) as e:
                            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                                raise
                            
                # Handle writable sockets
                for sock in writable:
                    if sock is client_socket and client_buffer:
                        sent = sock.send(client_buffer)
                        client_buffer = client_buffer[sent:]

                    elif sock is proxy_socket and proxy_buffer:
                        sent = sock.send(proxy_buffer)
                        proxy_buffer = proxy_buffer[sent:]

                if Config.dev > 1:
                    logging.info(f"Relayed client->proxy: {len(proxy_buffer)} bytes")
                    logging.info(f"Relayed proxy->client: {len(client_buffer)} bytes")

        except Exception as e:
            logging.error(f"Relay error: {str(e)}")
        finally:
            # Clean up
            client_socket.close()
            proxy_socket.close()

    def get_working_proxies(self):
        """Retrieve working proxies with valid latency."""
        return [p for p in self.proxy_manager.proxies if isinstance(p.get('latency'), (float, int)) and p['latency'] < float('inf')]


# Function to start the proxy server
def start_proxy_server(proxy_manager):
    class DebugHTTPServer(HTTPServer):
        def handle_error(self, request, client_address):
            logging.error(f"[SERVER ERROR] Request from {client_address}")
            super().handle_error(request, client_address)

        def get_request(self):
            sock, addr = super().get_request()
            logging.info(f"[RAW CONNECTION] New connection from {addr}")
            return sock, addr

    server = DebugHTTPServer(('0.0.0.0', 8080), ProxyHandler)
    ProxyHandler.proxy_manager = proxy_manager
    logging.info("[*] Debug proxy switcher listening on 0.0.0.0:8080")
    server.serve_forever()


# Function to start the threaded tasks
def start_threads(proxy_manager):
    # Start the UI thread
    ui_thread = threading.Thread(target=start_ui, args=(proxy_manager,), daemon=True)
    ui_thread.start()
    
    # Start the local proxy server thread
    proxy_server_thread = threading.Thread(target=start_proxy_server, args=(proxy_manager,), daemon=True)
    proxy_server_thread.start()

    # Create event loop for async tasks
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def initial_setup():
        """Initial proxy fetch and test on startup"""
        if Config.fetching:
            logging.info("Performing initial proxy fetch...")
            proxy_manager.fetch_proxies_from_apis()
        if Config.testing:
            logging.info("Performing initial proxy tests...")
            await proxy_manager.test_proxies()

    async def run_proxy_tests():
        while True:
            if Config.testing:
                logging.info("Starting regular proxy tests...")
                await proxy_manager.test_proxies()
            await asyncio.sleep(PROXY_TEST_INTERVAL)

    async def run_failed_proxy_retests():
        while True:
            if Config.retest:
                logging.info("Starting failed proxy retests...")
                await proxy_manager.test_proxies(retest=True)
            await asyncio.sleep(PROXY_TEST_INTERVAL*5)

    async def run_proxy_fetching():
        while True:
            if Config.fetching:
                logging.info("Fetching new proxies...")
                proxy_manager.fetch_proxies_from_apis()
                # Trigger immediate test of new proxies
                if Config.testing:
                    await proxy_manager.test_proxies()
            await asyncio.sleep(PROXY_FETCH_INTERVAL)

    # Run initial setup first
    loop.run_until_complete(initial_setup())

    # Then schedule periodic tasks
    loop.create_task(run_proxy_tests())
    loop.create_task(run_failed_proxy_retests())
    loop.create_task(run_proxy_fetching())
    loop.run_forever()



# Main function
# Start the command input thread in the main function
def main():
    proxy_manager = ProxyManager()
    
    # Load existing proxies
    proxy_manager.load_proxies()
    proxy_manager.load_timedout_proxies()
    proxy_manager.load_failed_proxies()
    
    # Start the threaded tasks
    start_threads(proxy_manager)

if __name__ == "__main__":
    main()