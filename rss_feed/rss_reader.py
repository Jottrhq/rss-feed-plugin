from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
                            QTextBrowser, QPushButton, QInputDialog, QMessageBox,
                            QComboBox, QListWidgetItem, QDialog)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
import feedparser
import json
import os
import requests
import threading
from feed_manager_dialog import FeedManagerDialog
try:
    from jottr.translation_manager import _
except ImportError:  # Jottr before the src/jottr package layout
    from translation_manager import _

# Former defaults that no longer resolve or return usable feed content.
_REMOVED_DEFAULT_FEED_URLS = {
    "https://feeds.reuters.com/reuters/topNews",
    "https://apnews.com/feed",
    "https://apnews.com/hub/world-news/feed",
    "https://apnews.com/hub/middle-east/feed",
}


# Enhanced headers especially for RSSHub
_FEED_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/rss+xml, application/xml, application/json, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Connection': 'keep-alive'
}


def fetch_feed(url):
    # Special handling for RSSHub
    if 'rsshub.app' in url:
        # Try direct feedparser first
        feed = feedparser.parse(url)
        if hasattr(feed, 'entries') and feed.entries:
            return feed
    response = requests.get(url, timeout=10, headers=_FEED_HEADERS)
    response.raise_for_status()
    return feedparser.parse(response.text)


class RSSReader(QWidget):
    # (request id, feed title, parsed feed or None, exception or None)
    feed_fetched = pyqtSignal(int, str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fetch_id = 0
        self.feed_fetched.connect(self.on_feed_fetched)
        self.feeds = {
            "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
            "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
            "CNN Top Stories": "http://rss.cnn.com/rss/edition.rss",
        }
        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        config_dir = os.path.join(config_home, "Jottr")
        os.makedirs(config_dir, exist_ok=True)
        self.feed_file = os.path.join(config_dir, "rss_feeds.json")
        self.setup_ui()
        self.load_feeds()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Top controls layout
        controls_layout = QHBoxLayout()
        
        # Feed selector dropdown
        self.feed_selector = QComboBox()
        self.feed_selector.currentTextChanged.connect(self.on_feed_selected)
        controls_layout.addWidget(self.feed_selector)
        
        # Buttons
        add_button = QPushButton(_("Add Feed"))
        remove_button = QPushButton(_("Remove Feed"))
        refresh_button = QPushButton(_("Refresh"))
        manage_button = QPushButton(_("Manage Feeds"))
        
        add_button.clicked.connect(self.add_feed)
        remove_button.clicked.connect(self.remove_feed)
        refresh_button.clicked.connect(self.refresh_feeds)
        manage_button.clicked.connect(self.manage_feeds)
        
        controls_layout.addWidget(manage_button)
        controls_layout.addWidget(add_button)
        controls_layout.addWidget(remove_button)
        controls_layout.addWidget(refresh_button)
        controls_layout.addStretch()
        
        layout.addLayout(controls_layout)
        
        # Feed entries list
        self.entries_list = QListWidget()
        self.entries_list.currentItemChanged.connect(self.show_entry)
        layout.addWidget(self.entries_list)
        
        # Content viewer
        self.content_viewer = QTextBrowser()
        self.content_viewer.setOpenExternalLinks(True)
        layout.addWidget(self.content_viewer)
        
        # Set size ratio between list and content
        layout.setStretch(1, 1)
        layout.setStretch(2, 2)
        
    def load_feeds(self):
        # Migrate feeds previously saved relative to the process cwd.
        legacy_feed_file = "rss_feeds.json"
        sources = []
        if os.path.exists(self.feed_file):
            sources.append(self.feed_file)
        if os.path.abspath(legacy_feed_file) != os.path.abspath(self.feed_file) and os.path.exists(legacy_feed_file):
            sources.append(legacy_feed_file)

        for path in sources:
            try:
                with open(path, 'r') as f:
                    loaded_feeds = json.load(f)
                    self.feeds.update(loaded_feeds)  # Merge with default feeds
            except Exception:
                pass  # Keep default feeds if file load fails

        self.feeds = {
            title: url for title, url in self.feeds.items()
            if url not in _REMOVED_DEFAULT_FEED_URLS
        }

        self.save_feeds()  # Save combined feeds
        self.update_feed_selector()
        
    def save_feeds(self):
        with open(self.feed_file, 'w') as f:
            json.dump(self.feeds, f)
            
    def update_feed_selector(self, select=None):
        selected = select or self.feed_selector.currentText()
        self.feed_selector.blockSignals(True)
        self.feed_selector.clear()
        self.feed_selector.addItems(sorted(self.feeds.keys()))
        index = self.feed_selector.findText(selected)
        if index >= 0:
            self.feed_selector.setCurrentIndex(index)
        self.feed_selector.blockSignals(False)
        self.refresh_current_feed()

    def on_feed_selected(self, feed_title):
        self.refresh_current_feed()

    def refresh_current_feed(self):
        self._fetch_id += 1
        self.entries_list.clear()
        self.content_viewer.clear()

        feed_title = self.feed_selector.currentText()
        if not feed_title or feed_title not in self.feeds:
            return

        self.content_viewer.setPlainText(_("Loading {feed_title}...").format(feed_title=feed_title))
        threading.Thread(
            target=self._fetch_in_background,
            args=(self._fetch_id, feed_title, self.feeds[feed_title]),
            daemon=True,
        ).start()

    def _fetch_in_background(self, fetch_id, feed_title, url):
        feed, error = None, None
        try:
            feed = fetch_feed(url)
        except Exception as e:
            error = e
        try:
            self.feed_fetched.emit(fetch_id, feed_title, feed, error)
        except RuntimeError:
            pass  # The reader was closed while the feed was loading

    def on_feed_fetched(self, fetch_id, feed_title, feed, error):
        if fetch_id != self._fetch_id:
            return  # A newer selection or refresh superseded this fetch
        self.content_viewer.clear()

        if error is not None:
            if isinstance(error, requests.exceptions.HTTPError) and error.response.status_code == 429:
                print(f"Rate limit headers: {error.response.headers}")  # Debug rate limit info
                message = _("Rate limit exceeded for {feed_title}. Please try again later.").format(feed_title=feed_title)
            else:
                print(f"Error fetching feed {feed_title}: {str(error)}")
                message = _("Could not fetch feed {feed_title}: {error}").format(feed_title=feed_title, error=str(error))
            self.content_viewer.setPlainText(message)
            return

        if hasattr(feed, 'entries') and feed.entries:
            for entry in feed.entries:
                item_text = entry.title if hasattr(entry, 'title') else 'No Title'
                list_item = QListWidgetItem(item_text)
                list_item.setData(Qt.ItemDataRole.UserRole, entry)
                self.entries_list.addItem(list_item)
        else:
            print(f"Feed {feed_title} has no entries. Feed status: {feed.get('status', 'unknown')}")
            print(f"Feed bozo: {feed.get('bozo', 'unknown')}")
            if hasattr(feed, 'debug_message'):
                print(f"Feed debug: {feed.debug_message}")
            self.content_viewer.setPlainText(_("No entries found in feed: {feed_title}").format(feed_title=feed_title))

    def refresh_feeds(self):
        self.refresh_current_feed()
            
    def add_feed(self):
        title, ok = QInputDialog.getText(self, _("Add RSS Feed"), _("Feed Title:"))
        if ok and title:
            url, ok = QInputDialog.getText(self, _("Add RSS Feed"), _("Feed URL:"))
            if ok and url:
                try:
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    
                    feed = feedparser.parse(response.text)
                    if hasattr(feed, 'entries') and feed.entries:
                        self.feeds[title] = url
                        self.save_feeds()
                        self.update_feed_selector(select=title)
                    else:
                        QMessageBox.warning(self, _("Error"), _("Invalid RSS feed"))
                except Exception as e:
                    QMessageBox.warning(self, _("Error"), _("Could not parse RSS feed: {error}").format(error=str(e)))
                    
    def remove_feed(self):
        current_feed = self.feed_selector.currentText()
        if current_feed:
            reply = QMessageBox.question(self, _("Remove Feed"),
                                       _('Remove feed "{title}"?').format(title=current_feed),
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                del self.feeds[current_feed]
                self.save_feeds()
                self.update_feed_selector()
                
    def show_entry(self, current, previous):
        if current:
            entry = current.data(Qt.ItemDataRole.UserRole)
            content = f"<h2>{entry.title}</h2>"
            if hasattr(entry, 'published'):
                content += f"<p><i>{_('Published:')} {entry.published}</i></p>"
            if hasattr(entry, 'description'):
                content += f"<p>{entry.description}</p>"
            if hasattr(entry, 'link'):
                content += f'<p><a href="{entry.link}">{_("Read more...")}</a></p>'
            self.content_viewer.setHtml(content)

    def manage_feeds(self):
        dialog = FeedManagerDialog(self.feeds, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.feeds = dialog.get_feeds()
            self.save_feeds()
            self.update_feed_selector()
