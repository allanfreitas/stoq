# -*- Mode: Python; coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005-2007 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   Evandro Vale Miquelito      <evandro@async.com.br>
##              Henrique Romano             <henrique@async.com.br>
##              Johan Dahlin                <jdahlin@async.com.br>
#
""" Main gui definition for purchase application.  """

import gettext
import datetime
from decimal import Decimal

import pango
import gtk
from kiwi.datatypes import currency
from kiwi.enums import SearchFilterPosition
from kiwi.python import all
from kiwi.ui.objectlist import Column, SearchColumn
from kiwi.ui.search import ComboSearchFilter
from stoqlib.database.runtime import (new_transaction, rollback_and_begin,
                                      finish_transaction)
from stoqlib.lib.message import warning, yesno
from stoqlib.domain.payment.operation import register_payment_operations
from stoqlib.domain.purchase import PurchaseOrder, PurchaseOrderView
from stoqlib.gui.search.consignmentsearch import ConsignmentItemSearch
from stoqlib.gui.search.personsearch import SupplierSearch, TransporterSearch
from stoqlib.gui.search.purchasesearch import PurchasedItemsSearch
from stoqlib.gui.wizards.purchasewizard import PurchaseWizard
from stoqlib.gui.wizards.consignmentwizard import (ConsignmentWizard,
                                                   CloseInConsignmentWizard)
from stoqlib.gui.wizards.purchasefinishwizard import PurchaseFinishWizard
from stoqlib.gui.wizards.purchasequotewizard import (QuotePurchaseWizard,
                                                     ReceiveQuoteWizard)
from stoqlib.gui.search.categorysearch import (SellableCategorySearch,
                                               BaseSellableCatSearch)
from stoqlib.gui.search.productsearch import (ProductSearch,
                                              ProductStockSearch,
                                              ProductsSoldSearch)
from stoqlib.gui.search.servicesearch import ServiceSearch
from stoqlib.gui.dialogs.purchasedetails import PurchaseDetailsDialog
from stoqlib.gui.dialogs.stockcostdialog import StockCostDialog
from stoqlib.gui.dialogs.productiondialog import ProductionDialog
from stoqlib.reporting.purchase import PurchaseReport
from stoqlib.lib.validators import format_quantity

from stoq.gui.application import SearchableAppWindow

_ = gettext.gettext

class PurchaseApp(SearchableAppWindow):
    app_name = _('Purchase')
    app_icon_name = 'stoq-purchase-app'
    gladefile = "purchase"
    search_table = PurchaseOrderView
    search_label = _('matching:')
    klist_selection_mode = gtk.SELECTION_MULTIPLE

    def __init__(self, app):
        SearchableAppWindow.__init__(self, app)
        self._setup_widgets()
        self._update_view()

    #
    # SearchableAppWindow
    #

    def create_filters(self):
        self.set_text_field_columns(['supplier_name'])
        self.status_filter = ComboSearchFilter(_('Show orders with status'),
                                               self._get_status_values())
        self.status_filter.select(PurchaseOrder.ORDER_CONFIRMED)
        self.add_filter(self.status_filter, SearchFilterPosition.TOP, ['status'])

    def get_columns(self):
        return [SearchColumn('id', title=_('Number'), sorted=True,
                             data_type=int, justify=gtk.JUSTIFY_RIGHT,
                             width=80),
                Column('status_str', title=_(u'Status'), data_type=str,
                       visible=False),
                SearchColumn('open_date', title=_('Opened'),
                              long_title='Date Opened',
                              data_type=datetime.date),
                SearchColumn('supplier_name', title=_('Supplier'),
                             data_type=str, searchable=True, width=200,
                             expand=True, ellipsize=pango.ELLIPSIZE_END),
                SearchColumn('ordered_quantity', title=_('Ordered'),
                             data_type=Decimal, width=90,
                             format_func=format_quantity),
                SearchColumn('received_quantity', title=_('Received'),
                             data_type=Decimal, width=90,
                             format_func=format_quantity),
                SearchColumn('total', title=_('Total'),
                             data_type=currency, width=110)]

    #
    # Private
    #

    def _setup_widgets(self):
        self.search.set_summary_label(column='total',
                                      label=_('<b>Orders Total:</b>'),
                                      format='<b>%s</b>')
        self.SendToSupplier.set_sensitive(False)
        # FIXME: enable before release.
        # XXX: Figure out if ideale still needs this. otherwise, remove the
        # related code
        self.Production.set_sensitive(False)

    def _update_totals(self):
        self._update_view()

    def _update_list_aware_widgets(self, has_items):
        for widget in (self.edit_button, self.details_button,
                       self.print_button):
            widget.set_sensitive(has_items)

    def _update_view(self):
        self._update_list_aware_widgets(len(self.results))
        selection = self.results.get_selected_rows()
        can_edit = one_selected = len(selection) == 1
        can_finish = False
        if selection:
            can_send_supplier = all(
                order.status == PurchaseOrder.ORDER_PENDING
                for order in selection)
            can_cancel = all(order_view.purchase.can_cancel()
                for order_view in selection)
        else:
            can_send_supplier = False
            can_cancel = False

        if one_selected:
            can_edit = (selection[0].status == PurchaseOrder.ORDER_PENDING or
                        selection[0].status == PurchaseOrder.ORDER_QUOTING)
            can_finish = (selection[0].status == PurchaseOrder.ORDER_CONFIRMED and
                          selection[0].received_quantity > 0)

        self.cancel_button.set_sensitive(can_cancel)
        self.edit_button.set_sensitive(can_edit)
        self.SendToSupplier.set_sensitive(can_send_supplier)
        self.details_button.set_sensitive(one_selected)
        self.FinishOrder.set_sensitive(can_finish)

    def _open_order(self, order=None, edit_mode=False):
        trans = new_transaction()
        order = trans.get(order)
        model = self.run_dialog(PurchaseWizard, trans, order,
                                edit_mode)
        rv = finish_transaction(trans, model)
        trans.close()

        return model

    def _edit_order(self):
        selected = self.results.get_selected_rows()
        qty = len(selected)
        if qty != 1:
            raise ValueError('You should have only one order selected, '
                             'got %d instead' % qty )
        purchase = selected[0].purchase
        if purchase.status == PurchaseOrder.ORDER_PENDING:
            self._open_order(purchase, edit_mode=False)
        else:
            self._quote_order(purchase)
        self.refresh()

    def _run_details_dialog(self):
        order_views = self.results.get_selected_rows()
        qty = len(order_views)
        if qty != 1:
            raise ValueError('You should have only one order selected '
                             'at this point, got %d' % qty)
        self.run_dialog(PurchaseDetailsDialog, self.conn,
                        model=order_views[0].purchase)

    def _send_selected_items_to_supplier(self):
        rollback_and_begin(self.conn)

        orders = self.results.get_selected_rows()
        valid_order_views = [
            order for order in orders
                      if order.status == PurchaseOrder.ORDER_PENDING]

        if not valid_order_views:
            warning(_("There are no orders with status "
                      "pending in the selection"))
            return
        elif len(valid_order_views) > 1:
            msg = (_("The %d selected orders will be marked as sent.")
                   % len(valid_order_views))
        else:
            msg = _('The selected order will be marked as sent.')
        if yesno(msg, gtk.RESPONSE_NO, _(u"Don't Send"), _(u"Send to supplier")):
            return

        trans = new_transaction()
        for order_view in valid_order_views:
            order = trans.get(order_view.purchase)
            order.confirm()
        trans.commit()
        self.refresh()

    def _print_selected_items(self):
        items = self.results.get_selected_rows() or self.results
        self.print_report(PurchaseReport, self.results,
                          self.status_filter.get_state().value)

    def _cancel_order(self):
        register_payment_operations()
        order_views = self.results.get_selected_rows()
        assert all(ov.purchase.can_cancel() for ov in order_views)
        if yesno(
            _('The selected order(s) will be cancelled.'),
            gtk.RESPONSE_NO, _(u"Don't Cancel"), _(u"Cancel order(s)")):
            return
        trans = new_transaction()
        for order_view in order_views:
            order = trans.get(order_view.purchase)
            order.cancel()
        trans.commit()
        self._update_totals()
        self.refresh()

    def _get_status_values(self):
        items = [(text, value)
                    for value, text in PurchaseOrder.statuses.items()]
        items.insert(0, (_('Any'), None))
        return items

    def _quote_order(self, quote=None):
        trans = new_transaction()
        quote = trans.get(quote)
        model = self.run_dialog(QuotePurchaseWizard, trans, quote)
        rv = finish_transaction(trans, model)
        trans.close()

    def _finish_order(self):
        order_views = self.results.get_selected_rows()
        qty = len(order_views)
        if qty != 1:
            raise ValueError('You should have only one order selected '
                             'at this point, got %d' % qty)

        trans = new_transaction()
        order = trans.get(order_views[0].purchase)
        model = self.run_dialog(PurchaseFinishWizard, trans, order)
        rv = finish_transaction(trans, model)
        trans.close()

        self.refresh()

    #
    # Kiwi Callbacks
    #

    def key_control_a(self, *args):
        # FIXME Remove this method after gazpacho bug fix.
        self._open_order()

    def on_results__row_activated(self, klist, purchase_order_view):
        self._run_details_dialog()

    def on_results__selection_changed(self, results, selected):
        self._update_view()

    def _on_results__double_click(self, results, order):
        self._run_details_dialog()

    def _on_results__has_rows(self, results, has_items):
        self._update_list_aware_widgets(has_items)

    def on_details_button__clicked(self, button):
        self._run_details_dialog()

    def on_edit_button__clicked(self, button):
        self._edit_order()

    def on_print_button__clicked(self, button):
        self._print_selected_items()

    def on_Categories__activate(self, action):
        self.run_dialog(SellableCategorySearch, self.conn)

    def on_SendToSupplier__activate(self, action):
        self._send_selected_items_to_supplier()

    def on_Production__activate(self, action):
        self.run_dialog(ProductionDialog, self.conn)

    def on_QuoteOrder__activate(self, action):
        self._quote_order()

    def on_NewConsignment__activate(self, action):
        trans = new_transaction()
        model = self.run_dialog(ConsignmentWizard, trans)
        rv = finish_transaction(trans, model)
        trans.close()

    def on_FinishOrder__activate(self, action):
        self._finish_order()

    def on_Quote__activate(self, action):
        self._quote_order()

    def on_SearchQuotes__activate(self, action):
        self.run_dialog(ReceiveQuoteWizard, self.conn)

    def on_CloseInConsignment__activate(self, action):
        trans = new_transaction()
        model = self.run_dialog(CloseInConsignmentWizard, trans)
        rv = finish_transaction(trans, model)
        trans.close()

    def on_SearchInConsignmentItems__activate(self, action):
        self.run_dialog(ConsignmentItemSearch, self.conn)

    def on_SearchPurchasedItems__activate(self, action):
        self.run_dialog(PurchasedItemsSearch, self.conn)

    def on_SearchStockItems__activate(self, action):
        self.run_dialog(ProductStockSearch, self.conn)

    # FIXME: Kiwi autoconnection OR rename, see #2323

    def _on_suppliers_action_clicked(self, action):
        self.run_dialog(SupplierSearch, self.conn, hide_footer=True)

    def _on_products_action_clicked(self, action):
        self.run_dialog(ProductSearch, self.conn, hide_price_column=True)

    def _on_order_action_clicked(self, action):
        self._open_order()
        self.refresh()

    def _on_base_categories_action_clicked(self, action):
        self.run_dialog(BaseSellableCatSearch, self.conn)

    def _on_services_action_clicked(self, action):
        self.run_dialog(ServiceSearch, self.conn, hide_price_column=True)

    def _on_transporters_action_clicked(self, action):
        self.run_dialog(TransporterSearch, self.conn, hide_footer=True)

    def on_cancel_button__clicked(self, button):
        self._cancel_order()

    def on_stock_cost_action__activate(self, action):
        self.run_dialog(StockCostDialog, self.conn, None)

    def on_ProductsSoldSearch__activate(self, action):
        self.run_dialog(ProductsSoldSearch, self.conn)

